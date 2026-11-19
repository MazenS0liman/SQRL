import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { HyperparameterSpec } from "@/types";

/** Sentinel used by the UI for a nullable parameter's "None" option. */
export const NULL_OPTION = "__null__";

/** Raw form values. Everything is kept as a string until submit time. */
export type HyperparameterValues = Record<string, string>;

export function defaultHyperparameterValues(
  specs: HyperparameterSpec[],
): HyperparameterValues {
  const values: HyperparameterValues = {};
  for (const spec of specs) {
    if (spec.default === null || spec.default === undefined) {
      values[spec.name] = spec.nullable ? NULL_OPTION : "";
    } else if (typeof spec.default === "boolean") {
      values[spec.name] = spec.default ? "true" : "false";
    } else {
      values[spec.name] = String(spec.default);
    }
  }
  return values;
}

/** Convert values returned by the API into editable form values. */
export function toRawHyperparameterValues(
  specs: HyperparameterSpec[],
  values: Record<string, unknown> | null | undefined,
): HyperparameterValues {
  const raw = defaultHyperparameterValues(specs);
  if (!values) return raw;

  for (const spec of specs) {
    const value = values[spec.name];
    if (value === undefined) continue;
    if (value === null) {
      raw[spec.name] = spec.nullable ? NULL_OPTION : "";
    } else if (typeof value === "boolean") {
      raw[spec.name] = value ? "true" : "false";
    } else if (typeof value === "object") {
      raw[spec.name] = JSON.stringify(value);
    } else {
      raw[spec.name] = String(value);
    }
  }
  return raw;
}

/**
 * Coerce validated form strings into the JSON payload expected by the API.
 * ``epochs`` is intentionally omitted — the page owns that as a top-level
 * training option, matching ``BuildRequest.epochs`` /
 * ``RetrainModelRequest.epochs`` on the backend.
 */
export function toApiHyperparameters(
  specs: HyperparameterSpec[],
  values: HyperparameterValues,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const spec of specs) {
    if (spec.name === "epochs") continue;

    const raw = values[spec.name];
    if (raw === undefined || raw === "" || raw === NULL_OPTION) {
      if (spec.nullable) payload[spec.name] = null;
      continue;
    }

    if (spec.type === "int") {
      payload[spec.name] = Math.trunc(Number(raw));
    } else if (spec.type === "float") {
      payload[spec.name] = Number(raw);
    } else if (spec.type === "bool") {
      payload[spec.name] = raw === "true";
    } else {
      payload[spec.name] = raw;
    }
  }
  return payload;
}

/** Return a user-facing error message, or null when the form is valid. */
export function validateHyperparameterValues(
  specs: HyperparameterSpec[],
  values: HyperparameterValues,
): string | null {
  for (const spec of specs) {
    if (spec.name === "epochs") continue;

    const raw = values[spec.name];
    if (raw === undefined || raw === "" || raw === NULL_OPTION) {
      if (!spec.nullable) return `${spec.label} cannot be empty.`;
      continue;
    }

    if (spec.type === "int" || spec.type === "float") {
      const number = Number(raw);
      if (!Number.isFinite(number)) return `${spec.label} must be a number.`;
      if (spec.type === "int" && !Number.isInteger(number)) {
        return `${spec.label} must be a whole number.`;
      }
      if (spec.min != null && number < spec.min) {
        return `${spec.label} must be at least ${spec.min}.`;
      }
      if (spec.max != null && number > spec.max) {
        return `${spec.label} must be at most ${spec.max}.`;
      }
    } else if (spec.type === "bool") {
      if (raw !== "true" && raw !== "false") return `${spec.label} must be true or false.`;
    } else if (spec.choices?.length) {
      const allowed = spec.choices.map((choice) =>
        choice === null ? NULL_OPTION : String(choice),
      );
      if (!allowed.includes(raw)) return `Choose a valid value for ${spec.label}.`;
    }
  }
  return null;
}

export interface ModelTrainingFieldsProps {
  specs: HyperparameterSpec[];
  values: HyperparameterValues;
  onChange: (name: string, value: string) => void;
  disabled?: boolean;
  compact?: boolean;
  idPrefix?: string;
}

/**
 * Shared hyperparameter renderer for the build and retrain flows.
 *
 * Numeric values stay as strings in local state so partially typed input
 * (e.g. "0.") doesn't turn into NaN while the user is still editing.
 */
export function ModelTrainingFields({
  specs,
  values,
  onChange,
  disabled = false,
  compact = false,
  idPrefix = "model-param",
}: ModelTrainingFieldsProps) {
  const visible = specs.filter((spec) => spec.name !== "epochs");
  if (visible.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        This model has no extra hyperparameters. Defaults will be used.
      </p>
    );
  }

  return (
    <div className={cn("grid gap-3", compact ? "sm:grid-cols-2" : "sm:grid-cols-2")}>
      {visible.map((spec) => {
        const id = `${idPrefix}-${spec.name}`;
        const value = values[spec.name] ?? (spec.nullable ? NULL_OPTION : "");
        const numeric = spec.type === "int" || spec.type === "float";

        // A nullable enum may advertise ``null`` both via ``nullable`` and
        // inside ``choices``. Deduplicate so "None" only renders once.
        const enumOptions: { value: string; label: string }[] = [];
        if (spec.type === "enum") {
          const seen = new Set<string>();
          if (spec.nullable) {
            enumOptions.push({ value: NULL_OPTION, label: "None" });
            seen.add(NULL_OPTION);
          }
          for (const choice of spec.choices ?? []) {
            const option = choice === null ? NULL_OPTION : String(choice);
            if (seen.has(option)) continue;
            seen.add(option);
            enumOptions.push({
              value: option,
              label: choice === null ? "None" : String(choice),
            });
          }
        }

        return (
          <div key={spec.name} className="space-y-1.5">
            <Label
              htmlFor={id}
              className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground"
            >
              {spec.label}
              {fitBadge(spec)}
            </Label>

            {spec.type === "enum" ? (
              <Select
                value={value}
                disabled={disabled}
                onValueChange={(next) => onChange(spec.name, next)}
              >
                <SelectTrigger id={id} className={cn(compact && "h-8 text-xs")}>
                  <SelectValue placeholder="Choose value" />
                </SelectTrigger>
                <SelectContent>
                  {enumOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : spec.type === "bool" ? (
              <Select
                value={value || "false"}
                disabled={disabled}
                onValueChange={(next) => onChange(spec.name, next)}
              >
                <SelectTrigger id={id} className={cn(compact && "h-8 text-xs")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="true">True</SelectItem>
                  <SelectItem value="false">False</SelectItem>
                </SelectContent>
              </Select>
            ) : (
              <Input
                id={id}
                type={numeric ? "number" : "text"}
                inputMode={numeric ? "decimal" : undefined}
                min={spec.min ?? undefined}
                max={spec.max ?? undefined}
                step={spec.step ?? (spec.type === "int" ? 1 : undefined)}
                value={value}
                disabled={disabled}
                placeholder={spec.nullable ? "Empty uses None" : undefined}
                onChange={(event) => onChange(spec.name, event.target.value)}
                className={cn(compact && "h-8 px-2 py-1 text-xs")}
              />
            )}

            {spec.description && (
              <p className="text-[11px] leading-snug text-muted-foreground">
                {spec.description}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function fitBadge(spec: HyperparameterSpec) {
  if (spec.name === "batch_size" || spec.fit_param) {
    return (
      <span className="ml-1.5 rounded bg-secondary px-1.5 py-0.5 font-mono text-[9px] normal-case tracking-normal text-muted-foreground">
        fit
      </span>
    );
  }
  return null;
}
