#!/usr/bin/python
"""
Exploratory Prompt Generator Module
===================================

Overview
--------

A module consisting of methods for generating a prompt template for explatory agents.

Revision notes
--------------
``_user_generate_hypotheses_prompt`` now accepts an optional
``previous_questions`` kwarg (a list of question strings already answered
for the same source set, e.g. from earlier cells in a notebook). When
provided, an "Already analyzed" block is injected into the prompt with an
explicit instruction not to repeat or closely paraphrase them, so a second
EDA sweep over the same data produces analytically distinct hypotheses
instead of duplicating earlier charts.
"""
# ——————————————————————————————————————————————————————————————
# Imports

import json
import textwrap
from enum import Enum
from typing import Dict, List, Optional
from typing_extensions import override

# Abstract Class
from squirrel.modules.prompts.abstract import IPromptGenerator

# Schemas
from squirrel.schemas.eda import Hypothesis, ChartResult

# —— Prompt type discriminator ——————————————————————————————————————————
class ExplorePromptType(Enum):
    """
    
    
    
    """
    GENERATE_DOMAIN_CONTEXT = "generate_domain_context"
    EXTRACT_ENTITIES_AND_METRICS = "extract_entities_and_metrics"
    GENERATE_HYPOTHESES = "generate_hypotheses"
    GENERATE_SQL = "generate_sql"
    GENERATE_OBSERVATION = "generate_observation"
    GENERATE_SUMMARY = "generate_summary"
    CLASSIFY_QUESTION_INTENT = "classify_question_intent"
    GENERATE_VIEW_SQL = "generate_view_sql"

# —— Prompt generator implementation —————————————————————————————————————————
class ExploratoryPromptGenerator(IPromptGenerator):
    """
    
    
    """
    
    def __init__(self):
        super().__init__()

    # ── System prompts ────────────────────────────────────────────────────────
    
    @override
    def generate_system_prompt(
        self,
        prompt_type: ExplorePromptType | None = None
    ) -> str:
        """
        Generate stage-specific system-level instructions.
        
        :param prompt_type: type of prompt to generate.
        :type prompt_type: ExplorePromptType
        
        :return: System prompt string.
        :rtype: str
        """
        dispatch = {
            ExplorePromptType.GENERATE_DOMAIN_CONTEXT: self._system_generate_domain_context_prompt,
            ExplorePromptType.EXTRACT_ENTITIES_AND_METRICS: self._system_extract_entities_and_metrics_prompt,
            ExplorePromptType.GENERATE_HYPOTHESES: self._system_generate_hypotheses_prompt,
            ExplorePromptType.GENERATE_SQL: self._system_generate_sql_prompt,
            ExplorePromptType.GENERATE_OBSERVATION: self._system_generate_observation_prompt,
            ExplorePromptType.GENERATE_SUMMARY: self._system_generate_summary_prompt,
            ExplorePromptType.CLASSIFY_QUESTION_INTENT: self._system_classify_question_intent_prompt,
            ExplorePromptType.GENERATE_VIEW_SQL: self._system_generate_view_sql_prompt,
        }

        if prompt_type is not None and prompt_type not in dispatch:
            raise ValueError(
                f"Unsupported prompt type: {prompt_type!r}."
                f"Expected one of: {[t.value for t in ExplorePromptType]}"
            )
        
        handler = dispatch.get(prompt_type, lambda: "You are a helpful assistant.")
        return handler()

    def _system_generate_domain_context_prompt(self) -> str:
        return textwrap.dedent("""
            You are a principal data scientist performing exploratory data analysis.

            STRICT RULES — follow them exactly:
            - Base every inference solely on the column names, types, and statistics provided.
            - Do NOT invent table names, column names, or KPI formulas that are not present in the schema.
            - If you are uncertain about the domain, use domain_confidence "low" and describe only what the schema directly supports.
            - KPI sql_hint must only reference column names present verbatim in the schema.
            - exploration_goals must be answerable given the actual columns; do not propose analyses that require columns not in the schema.
        """).strip()

    def _system_extract_entities_and_metrics_prompt(self) -> str:
        return textwrap.dedent("""
            You are a senior analytics engineer extracting metrics and dimensions from a database schema.

            STRICT RULES — follow them exactly:
            - Every metric sql_expression must use ONLY column names that appear verbatim in the provided schema.
            - Every dimension column must be an exact, verbatim column name from the schema — no paraphrasing, no inventing.
            - If no suitable numeric column exists for a metric, omit that metric rather than inventing a formula.
            - Do NOT create aliases, derived names, or columns not explicitly present in the schema.
        """).strip()

    def _system_generate_hypotheses_prompt(self) -> str:
        return textwrap.dedent("""
            You are a senior data scientist generating analytical hypotheses grounded strictly in a real database schema.

            STRICT RULES — follow them exactly:
            - Every hypothesis question must be answerable using ONLY columns that exist verbatim in the schema.
            - Do NOT assume columns exist unless they appear explicitly in the schema.
            - Do NOT propose joins, filters, or groupings using column names absent from the schema.
            - Each hypothesis must have genuine analytical depth — no simple counts, no plain aggregations, no bar-chart-of-totals.
            - If the schema is limited, generate fewer but fully grounded hypotheses rather than padding with unverifiable ones.
            - Every hypothesis must be analytically distinct from any hypotheses already analyzed for this data — do not repeat or closely paraphrase a prior question.
        """).strip()

    def _system_generate_sql_prompt(self) -> str:
        return textwrap.dedent("""
            You are an expert PostgreSQL analyst generating SQL that will be executed against a live database.

            STRICT RULES — follow them exactly:
            - Use ONLY column names listed verbatim in the AVAILABLE COLUMNS block. Any other identifier is a critical error.
            - Use ONLY table names listed verbatim in the schema. Do not invent, abbreviate, or paraphrase table names.
            - Every column referenced in FROM, JOIN ON, WHERE, GROUP BY, ORDER BY, and SELECT must be in AVAILABLE COLUMNS.
            - Aliases defined with AS are fine to use as output names in x/y — but the underlying source column must still be real.
            - When the hypothesis cannot be answered with the available columns, pivot to the closest answerable question and note it in a SQL comment.
            - PostgreSQL syntax only: no MySQL, no SQLite, no SQL Server dialects.
            - SELECT only — no DDL, DML, EXPLAIN, or CALL statements.
        """).strip()

    def _system_generate_observation_prompt(self) -> str:
        return textwrap.dedent("""
            You are a senior business analyst interpreting query results from a live database.

            STRICT RULES — follow them exactly:
            - Every claim must be directly supported by the query result rows provided. Do NOT invent numbers or trends.
            - If you state a percentage, rate, or absolute figure, it must be derivable from the data shown.
            - Do NOT assert causation — only describe correlation or patterns visible in the data.
            - Do NOT reference columns, entities, or metrics that are not present in the query results.
            - Keep language precise: "the data shows X" not "this suggests Y might be happening at some point".
        """).strip()

    def _system_generate_summary_prompt(self) -> str:
        return textwrap.dedent("""
            You are a chief data officer preparing a concise, evidence-based executive summary.

            STRICT RULES — follow them exactly:
            - Every finding must trace back to a specific observation listed in the input.
            - Do NOT introduce new insights, metrics, or claims that are not present in the provided observations.
            - Quantify findings when the observations contain numbers — do not say "significant" without citing a figure.
            - recommended_next_steps must be concrete and achievable, not generic advice like "collect more data".
            - dataset_description must reflect what the schema and observations actually contain.
        """).strip()

    def _system_classify_question_intent_prompt(self) -> str:
        return textwrap.dedent("""
            You decide how to present the answer to a data question: as a CHART
            or as a DATA TABLE (a saved view).

            Prefer a chart for trends, comparisons across a dimension,
            distributions, correlations, or proportions — anything a visual
            pattern helps with.

            Prefer a table when the question asks to "list", "show me", "find",
            "get me the rows/records", filter or look up a specific set of rows,
            wants raw/detailed data rather than an aggregate pattern, or
            explicitly asks to create/save/build a view, dataset, or extract.

            Do not guess columns — you don't have the schema here, only the
            question's phrasing.
        """).strip()

    def _system_generate_view_sql_prompt(self) -> str:
        return textwrap.dedent("""
            You are an expert PostgreSQL analyst who creates a NAMED VIEW that
            reshapes, filters, or aggregates existing tables to answer a data
            request. You are not charting anything.

            STRICT RULES — follow them exactly:
            - Output exactly one statement: CREATE OR REPLACE VIEW "<view_name>" AS SELECT ...
            - Use ONLY column and table names listed verbatim in AVAILABLE COLUMNS.
            - The view body is SELECT only — no INSERT, UPDATE, DELETE, DROP,
            ALTER, or TRUNCATE anywhere in the statement.
            - Never modify or drop an existing table — a view is a saved query,
            not a data mutation.
            - Do not put LIMIT inside the view definition — the caller previews
            it separately.
            - PostgreSQL syntax only.
        """).strip()

    # ── User prompts ──────────────────────────────────────────────────────────
    
    @override
    def generate_user_prompt(
        self, 
        prompt_type: ExplorePromptType | None = None, 
        **kwargs
    ) -> str:
        """
        Dispatch to the correct user prompt builder based on ``prompt_type``.

        :param kwargs: Must include ``prompt_type`` (InspectPromptType).
            Additional keys depend on the stage — see each builder's docstring.
            
        :return: User prompt string ready to send to the LLM.
        :rtype: str
        
        :raises ValueError: If ``prompt_type`` is missing or unrecognised.
        """
        dispatch = {
            ExplorePromptType.GENERATE_DOMAIN_CONTEXT: self._user_generate_domain_context_prompt,
            ExplorePromptType.EXTRACT_ENTITIES_AND_METRICS: self._user_extract_entities_and_metrics_prompt,
            ExplorePromptType.GENERATE_HYPOTHESES: self._user_generate_hypotheses_prompt,
            ExplorePromptType.GENERATE_SQL: self._user_generate_sql_prompt,
            ExplorePromptType.GENERATE_OBSERVATION: self._user_generate_observation_prompt,
            ExplorePromptType.GENERATE_SUMMARY: self._user_generate_summary_prompt,
            ExplorePromptType.CLASSIFY_QUESTION_INTENT: self._user_classify_question_intent_prompt,
            ExplorePromptType.GENERATE_VIEW_SQL: self._user_generate_view_sql_prompt,
        }
        
        handler = dispatch.get(prompt_type)
        
        if handler is None:
            raise ValueError(
                f"Unsupported prompt: {prompt_type!r}"
                f"Expected one of: {[t.value for t in ExplorePromptType]}"
            )
        return handler(**kwargs)

    def _user_generate_domain_context_prompt(self, **kwargs) -> str:
        """
        
        
        """
        schema_details: str = kwargs.get("schema_details", "")

        return textwrap.dedent(f"""
            Analyse the following database schema and statistics, then infer the business context.

            # Schema & Statistics:
            {schema_details}

            Return a JSON object with exactly these keys:

            {{
                "domain": "string — industry/business domain (e.g. e-commerce, SaaS, healthcare)",
                "domain_confidence": "high | medium | low",
                "main_entities": ["list of core business objects (e.g. Customer, Order, Product)"],
                "fact_tables":      ["tables containing transactional/event data"],
                "dimension_tables": ["tables containing descriptive/reference data"],
                "kpis": [
                    {{
                        "name": "KPI name",
                        "description": "what it measures",
                        "tables_involved": ["table names"],
                        "sql_hint": "rough SQL fragment"
                    }}
                ],
                "exploration_goals": ["list of high-value analytical questions for this domain"],
                "data_quality_flags": ["any concerns: sparse columns, likely PII, potential duplicates, …"]
            }}                             
        """)

    def _user_extract_entities_and_metrics_prompt(self, **kwargs) -> str:
        """
        
        
        
        """
        domain_context: str = kwargs.get("domain_context", "")
        schema_details: str = kwargs.get("schema_details", "")
        
        return textwrap.dedent(f"""
            # Domain Context:
            {json.dumps(domain_context, indent=2)}
           
            # Schema & Statistics
            {schema_details}
            
            # Identify
            1. **Measurable metrics** — numeric columns or SQL expressions that can be aggregated.
            2. **Grouping dimensions** — categorical/temporal columns useful for slicing metrics.
            
            ⚠️ IMPORTANT: Only reference columns that appear verbatim in the schema above.
            Do NOT invent or paraphrase column names.
            
            Return JSON:
            
                {{
                "metrics": [
                    {{
                    "name": "metric display name",
                    "sql_expression": "e.g. SUM(o.total_amount)",
                    "tables": ["orders"],
                    "description": "what it measures"
                    }}
                ],
                "dimensions": [
                    {{
                    "name": "dimension display name",
                    "column": "exact_column_name_from_schema",
                    "type": "temporal | categorical | geographic | boolean",
                    "description": "what it represents"
                    }}
                ]
                }}
        """)
    
    def _user_generate_hypotheses_prompt(self, **kwargs) -> str:
        domain_context: Dict = kwargs.get("domain_context", {})
        entities_and_metrics: Dict = kwargs.get("entities_and_metrics", {})
        schema_summary: str = kwargs.get("schema_summary", "")
        analytical_types: str = kwargs.get("analytical_types", "")
        plot_types: str = kwargs.get("plot_types", "")
        max_hypotheses: int = kwargs.get("max_hypotheses", 3)
        available_columns: str = kwargs.get("available_columns", "")
        previous_questions: List[str] = kwargs.get("previous_questions", [])

        column_block = (
            f"\n## AVAILABLE COLUMNS (use ONLY these exact names in questions):\n{available_columns}\n"
            if available_columns else ""
        )

        # De-duplication block — only rendered when there's history to avoid.
        # Capped at 50 entries so a notebook with a long cell history doesn't
        # blow the prompt budget; the most recent 50 already cover the
        # analytically "nearby" space far better than older ones would.
        previous_block = ""
        if previous_questions:
            listed = "\n".join(f"- {q}" for q in previous_questions[:50])
            previous_block = textwrap.dedent(f"""
                ## Already analyzed — DO NOT repeat these or close paraphrases:
                {listed}

                Every new hypothesis below must be analytically distinct from the
                list above — a different metric, a different grouping dimension,
                or a materially different analytical angle. Reusing the same
                metric+dimension pairing under reworded phrasing does NOT count
                as distinct.
            """)

        return textwrap.dedent(f"""
            ## Domain Context:
            {json.dumps(domain_context, indent=2)}

            ## Entities & Metrics:
            {json.dumps(entities_and_metrics, indent=2)}

            ## Schema:
            {schema_summary}
            {column_block}
            {previous_block}
            ## Your task:
            Generate exactly {max_hypotheses} HIGH-VALUE exploratory analyses.

            ## MANDATORY COLUMN CHECK (do this before writing each hypothesis):
            Before writing each hypothesis, verify that every column, measure, or filter
            it references appears in the AVAILABLE COLUMNS block above. If a column is
            not there, do NOT include it in the hypothesis.

            ## QUALITY RULES:
            - Each hypothesis must require genuine analytical reasoning — segmentation,
              cohort comparison, trend decomposition, outlier identification, etc.
            - Do NOT produce: simple counts, total-by-category summaries, or single-metric
              bar charts (e.g. "total sales by region").
            - Each hypothesis must be answerable with a single SQL query using only the
              available columns.
            - Prefer hypotheses that reveal non-obvious patterns or business risks.

            ## ANALYTICAL CATEGORIES (each hypothesis must use exactly one):
            {analytical_types}

            ## CHART TYPES (pick the one that best fits the analysis):
            {plot_types}

            Return a JSON array of exactly {max_hypotheses} objects:
            [
                {{
                    "id": "h_01",
                    "question": "precise analytical question referencing only real column names",
                    "title": "chart title (≤ 8 words)",
                    "description": "what the chart shows and why it is analytically interesting",
                    "why_it_matters": "specific business implication if the pattern is confirmed",
                    "plot_type": "one of: {plot_types}",
                    "analytical_type": "one of the categories listed above"
                }}
            ]
        """).strip()
        
    def _user_generate_sql_prompt(self, **kwargs) -> str:
        domain_context: str = kwargs.get("domain_context", "")
        hypothesis: Hypothesis = kwargs.get("hypothesis", None)
        schema_summary: str = kwargs.get("schema_summary", "")
        entities_and_metrics: Dict = kwargs.get("entities_and_metrics", {})
        columns_details: str = kwargs.get("columns_details", "")

        return textwrap.dedent(f"""
            ## Domain Context:
            {json.dumps(domain_context, indent=2)}

            ## Entities & Metrics:
            {json.dumps(entities_and_metrics, indent=2)}

            ## Full Schema:
            {schema_summary}

            ## AVAILABLE COLUMNS — YOU MUST USE ONLY THESE EXACT NAMES:
            {columns_details}

            (Each line is: table_name: col1, col2, col3 ...
             Use double-quotes around column names that contain uppercase letters or spaces, e.g. "CustomerID".)

            ## Analytical Question:
            {hypothesis.question}

            ## Analytical Type:
            {hypothesis.analytical_type}

            ## Required Chart Type:
            {hypothesis.plot_type}

            ## Instructions:
            Write a single PostgreSQL SELECT query that answers the analytical question above.

            Prefer when analytically appropriate:
            - CTEs (WITH ...) for multi-step logic
            - Window functions: LAG, LEAD, RANK, ROW_NUMBER, NTILE, running totals, moving averages
            - Date truncation: DATE_TRUNC for time-series grouping
            - Percentile functions: PERCENTILE_CONT, NTILE

            ## Hard constraints — violating any of these will cause a runtime error:
            1. ONLY use column names from AVAILABLE COLUMNS above. Every other identifier is forbidden.
            2. ONLY use table names that appear in the schema above.
            3. SELECT only — no INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, or EXPLAIN.
            4. LIMIT 1000 maximum.
            5. The x.column and y.column values in your JSON must exactly match an alias in your SELECT clause.
            6. If the question cannot be answered with the available columns, write the closest answerable
               question in a SQL comment (-- Pivot: ...) and answer that instead.

            ## Return JSON only (no markdown fences, no explanation):
            {{
                "sql": "SELECT ... FROM ... LIMIT 1000",
                "x": {{
                    "column": "alias_from_select_clause",
                    "label": "Human-Readable X Label",
                    "type": "temporal | quantitative | ordinal | nominal"
                }},
                "y": {{
                    "column": "alias_from_select_clause",
                    "label": "Human-Readable Y Label",
                    "type": "temporal | quantitative | ordinal | nominal"
                }},
                "group_by": "alias_for_color_grouping_or_null"
            }}
        """).strip()
        
    def _user_generate_observation_prompt(self, **kwargs) -> str:
        hypothesis: Hypothesis = kwargs.get("hypothesis", None)
        num_of_samples: int = kwargs.get("num_of_samples", 0)
        sample: List[Dict] = kwargs.get("sample", [])
        domain_context: Dict = kwargs.get("domain_context", {})
        column_names: List[str] = kwargs.get("column_names", [])

        if hypothesis is None:
            raise ValueError("Invalid Hypothesis")

        column_context = (
            f"\n## Columns in the result set (use ONLY these names when referring to data):\n"
            + ", ".join(column_names)
            if column_names else ""
        )

        domain_hint = ""
        if domain_context:
            domain_hint = (
                f"\n## Business domain: {domain_context.get('domain', 'unknown')}"
                f"\n## Main entities: {', '.join(domain_context.get('main_entities', []))}"
            )

        return textwrap.dedent(f"""
            ## Analytical Question:
            {hypothesis.question}

            ## Why it matters:
            {hypothesis.why_it_matters}
            {domain_hint}
            {column_context}

            ## Query result ({num_of_samples} rows shown):
            {json.dumps(sample, default=str)}

            ## Your task:
            Write a concise, grounded analytical observation in plain text (max 3 sentences).

            Rules:
            - Cite at least one specific number, value, or pattern from the data above.
            - Only reference column names that appear in the result set columns listed above.
            - Do NOT invent percentages, totals, or trends that cannot be computed from the rows shown.
            - Do NOT assert causation — describe what the data shows, not why it might be happening.
            - State one concrete business implication in the final sentence.

            Output plain text only — no bullet points, no headers, no markdown.
        """).strip()

    def _user_generate_summary_prompt(self, **kwargs) -> str:
        """
        
        
        
        """
        chart_results: ChartResult = kwargs.get("chart_results", None)
        
        observations_block: str = json.dumps(
            [
                {
                    "title":           r.hypothesis.title,
                    "question":        r.hypothesis.question,
                    "observation":     r.observation,
                    "analytical_type": r.hypothesis.analytical_type,
                }
                for r in chart_results
            ],
            indent=2,
        )
        
        return textwrap.dedent(f"""
           
           The following analytical observations were produced by an automated EDA pipeline:

           {observations_block}
           
           Synthesise these into an executive summary. Return JSON:

            {{
            "dataset_description": "2-3 sentences describing what this dataset contains and its analytical richness",
            "key_findings": [
                {{
                    "finding": "specific, quantified insight",
                    "source_analyses": ["hypothesis id(s) that support this finding"],
                    "business_impact": "high | medium | low"
                }}
            ],
            "recommended_next_steps": [
                "concrete, prioritised action (e.g. deep-dive, data collection, experiment)"
            ]
            }}
        """)

    def _user_classify_question_intent_prompt(self, **kwargs) -> str:
        question: str = kwargs.get("question", "")
        return textwrap.dedent(f"""
            Question: {question}

            Return JSON only:
            {{
                "needs_chart": true | false,
                "chart_type": "line | bar | scatter | boxplot | heatmap, or null if needs_chart is false",
                "reasoning": "one short sentence"
            }}
        """).strip()

    def _user_generate_view_sql_prompt(self, **kwargs) -> str:
        question: str = kwargs.get("question", "")
        view_name: str = kwargs.get("view_name", "")
        schema_summary: str = kwargs.get("schema_summary", "")
        columns_details: str = kwargs.get("columns_details", "")
        previous_error: Optional[str] = kwargs.get("previous_error")

        error_block = ""
        if previous_error:
            error_block = textwrap.dedent(f"""
                ⚠️ YOUR PREVIOUS ATTEMPT FAILED — FIX IT:
                {previous_error}
                Use only real column names from AVAILABLE COLUMNS below.
            """)

        return textwrap.dedent(f"""
            ## Schema:
            {schema_summary}

            ## AVAILABLE COLUMNS — YOU MUST USE ONLY THESE EXACT NAMES:
            {columns_details}
            {error_block}
            ## Request:
            {question}

            Return ONLY the SQL statement — no markdown fences, no explanation:
            CREATE OR REPLACE VIEW "{view_name}" AS SELECT ...
        """).strip()
