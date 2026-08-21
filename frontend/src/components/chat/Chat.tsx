import React, { useState, useRef, useEffect, useMemo } from "react";
import { ArrowUp, Square, Sparkles, Nut, Squirrel, LayoutDashboard, FileCodeIcon } from "lucide-react";
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import type { ChatMessage, NotebookDataSource, UploadedFile } from "@/types";

interface ChatProps {
    handleAddMessage: (message: ChatMessage) => void
    dataSources?: NotebookDataSource[];
    isProcessing?: boolean;
    onStop?: () => void;
    onGenerateDashboard?: () => void;
    onGeneratePrepScript?: () => void;
}

export function Chat({ 
    handleAddMessage, 
    dataSources = [], 
    isProcessing = false, 
    onStop,
    onGenerateDashboard,
    onGeneratePrepScript
}: ChatProps) {
    const [input, setInput] = useState("");
    const [showOptions, setShowOptions] = useState(false);
    const [pendingFiles, setPendingFiles] = useState<UploadedFile[]>([]);
    const [mentionQuery, setMentionQuery] = useState<string | null>(null);
    const [mentionStart, setMentionStart] = useState<number | null>(null);
    const [attachedSources, setAttachedSources] = useState<NotebookDataSource[]>([]);
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);
    const actionsRef = useRef<HTMLDivElement | null>(null);

    const menuItems = [
        onGenerateDashboard &&
        { key: "generate-deep-analysis", label: "Generate Deep Data Analysis", icon: LayoutDashboard, handler: onGenerateDashboard, accent: true },
        onGeneratePrepScript &&
        { key: "generate-prep-script", label: "Generate Data Preparation Script", icon: FileCodeIcon, handler: onGeneratePrepScript, accent: false }
    ].filter(Boolean) as { key: string; label: string; icon: typeof Sparkles; handler: () => void; accent?: boolean }[];

    const handleMenuSelect = (onSelect: () => void) => {
        setShowOptions(false);
        onSelect();
    };

    const mentionMatches = useMemo(() => {
        if (mentionQuery === null) return [];
        const q = mentionQuery.toLowerCase();
        return dataSources.filter((ds) => ds.label.toLowerCase().includes(q)).slice(0, 6);
    }, [mentionQuery, dataSources]);

    // Detect an in-progress "@word" right before the caret.
    const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        const value = e.target.value;
        setInput(value);

        const caret = e.target.selectionStart ?? value.length;
        const uptoCaret = value.slice(0, caret);
        const match = uptoCaret.match(/@([\w.-]*)$/);
        if (match) {
        setMentionQuery(match[1]);
        setMentionStart(caret - match[0].length);
        } else {
        setMentionQuery(null);
        setMentionStart(null);
        }
    };

    const attachSource = (ds: NotebookDataSource) => {
        setAttachedSources((prev) => (prev.some((s) => s.id === ds.id) ? prev : [...prev, ds]));
    };

    const detachSource = (id: string) => {
        setAttachedSources((prev) => prev.filter((s) => s.id !== id));
    };

    const insertMention = (ds: NotebookDataSource) => {
        if (mentionStart === null) return;
        const caret = textareaRef.current?.selectionStart ?? input.length;
        const before = input.slice(0, mentionStart);
        const after = input.slice(caret);
        const trimmedAfter = before.endsWith(" ") && after.startsWith(" ") ? after.slice(1) : after;
        const next = `${before}${trimmedAfter}`;
        setInput(next);
        setMentionQuery(null);
        setMentionStart(null);
        attachSource(ds);
        requestAnimationFrame(() => {
        textareaRef.current?.setSelectionRange(before.length, before.length);
        textareaRef.current?.focus();
        });
    };

    const handleSubmit = () => {
        if (isProcessing) return;
        if (!input.trim() && pendingFiles.length === 0) return;
        const attachedIds = attachedSources.map((s) => s.id);
        handleAddMessage({
        id: `user-${Date.now()}`,
        content: input.trim(),
        role: "user",
        timestamp: new Date(),
        type: pendingFiles.length > 0 ? "file" : "text",
        data: {
            ...(pendingFiles.length > 0 ? { files: pendingFiles } : {}),
            dataSourceIds: attachedIds.length > 0 ? attachedIds : undefined,
        },
        });
        setInput("");
        setPendingFiles([]);
        setAttachedSources([]);
    };

    // The main button does double duty: send while idle, stop while a task
    // is running for the message that was just sent.
    const handleMainButtonClick = () => {
        if (isProcessing) {
            onStop?.();
            return;
        }
        handleSubmit();
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (mentionQuery !== null && mentionMatches.length > 0) {
        if (e.key === "Escape") {
            setMentionQuery(null);
            return;
        }
        // Let Enter/Tab pick the first match instead of submitting while the
        // mention picker is open.
        if (e.key === "Enter" || e.key === "Tab") {
            e.preventDefault();
            insertMention(mentionMatches[0]);
            return;
        }
        }
        if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
        }
    };

    useEffect(() => {
        function onDocClick(e: MouseEvent) {
            if (!actionsRef.current) return;
            if (actionsRef.current.contains(e.target as Node)) return;
            setShowOptions(false);
        }
        if (showOptions) document.addEventListener('click', onDocClick);
        return () => document.removeEventListener('click', onDocClick);
    }, [showOptions]);

    const buttonDisabled = !isProcessing && !input.trim() && pendingFiles.length === 0;

    return (
        <div className="flex h-fit w-full flex-col rounded-lg border border-border bg-card shadow-sm">
            {/* Input */}
            <div className="p-4">
                <div className="flex items-end gap-3">
                    <div className="relative flex-1">
                        <Textarea
                            ref={textareaRef}
                            value={input}
                            onChange={handleInputChange}
                            onKeyDown={handleKeyDown}
                            placeholder="Ask me to analyze or edit your data — type @ to reference a specific source"
                            className="min-h-[44px] max-h-32 resize-none"
                            rows={1}
                        />
                        {mentionQuery !== null && mentionMatches.length > 0 && (
                            <div className="absolute bottom-full left-0 mb-1 w-64 rounded-md border border-border bg-card p-1 shadow-lg z-20">
                            {mentionMatches.map((ds) => (
                                <button
                                key={ds.id}
                                type="button"
                                onClick={() => insertMention(ds)}
                                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm text-foreground hover:bg-secondary"
                                >
                                <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                                    {ds.kind}
                                </span>
                                <span className="truncate">{ds.label}</span>
                                </button>
                            ))}
                            </div>
                        )}
                        {attachedSources.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                                {attachedSources.map((ds) => (
                                    <span
                                        key={ds.id}
                                        className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-1 text-xs text-foreground"
                                    >
                                        <span className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                                            {ds.kind}
                                        </span>
                                        <span className="max-w-[140px] truncate">{ds.label}</span>
                                        <button
                                            type="button"
                                            onClick={() => detachSource(ds.id)}
                                            className="text-muted-foreground hover:text-foreground"
                                            aria-label={`Remove ${ds.label} from this message`}
                                        >
                                            ×
                                        </button>
                                    </span>
                                ))}
                            </div>
                        )}
                        {pendingFiles.length > 0 && (
                            <div className="mt-2 rounded-lg border border-border bg-card px-3 py-2 text-sm shadow-sm">
                                <div className="mb-2 flex items-center justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="font-medium text-foreground">{pendingFiles.length} file{pendingFiles.length > 1 ? 's' : ''} attached</div>
                                    </div>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="shrink-0 text-muted-foreground hover:text-foreground hover:bg-secondary"
                                        onClick={() => setPendingFiles([])}
                                    >
                                        Clear all
                                    </Button>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    {pendingFiles.map((file) => (
                                        <div key={`${file.fileName}-${file.fileUrl}`} className="flex items-center gap-2 rounded-full border border-border/80 bg-background px-3 py-1.5 text-xs text-foreground">
                                            <span className="max-w-[180px] truncate">{file.fileName}</span>
                                            <button
                                                type="button"
                                                onClick={() => setPendingFiles((currentFiles) => currentFiles.filter((currentFile) => currentFile !== file))}
                                                className="text-muted-foreground hover:text-foreground"
                                                aria-label={`Remove ${file.fileName}`}
                                            >
                                                ×
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>

                    {menuItems.length > 0 && (
                        <div className="relative shrink-0" ref={actionsRef}>
                            <Button 
                                type="button"
                                variant="outline"
                                size="icon"
                                onClick={() => setShowOptions((v) => !v)}
                                disabled={isProcessing}
                                aria-label="More actions"
                                title="More actions"
                                className="h-11 w-11 shrink-0 rounded-md"
                            >
                                <Nut
                                    className={`h-4 w-4 transition-all duration-300`}
                                />
                            </Button>
                            {showOptions && (
                                <div className="absolute w-[max-content] bottom-full left-0 mb-2 w-60 rounded-lg border border-border bg-card p-1.5 shadow-lg z-20">
                                    {menuItems.map(({ key, label, icon: Icon, handler, accent }) => (
                                        <button
                                            key={key}
                                            type="button"
                                            onClick={() => handleMenuSelect(handler)}
                                            className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm text-foreground hover:bg-secondary"
                                        >
                                            <Icon className={`h-4 w-4 shrink-0 ${accent ? "text-primary" : "text-muted-foreground"}`} />
                                            <span>{label}</span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    <Button
                        onClick={handleMainButtonClick}
                        size="icon"
                        disabled={buttonDisabled}
                        aria-label={isProcessing ? "Stop" : "Send"}
                        title={isProcessing ? "Stop" : "Send"}
                        className={`h-11 w-11 shrink-0 rounded-md text-primary-foreground shadow-md transition-transform hover:scale-105 ${
                            isProcessing ? 'bg-destructive' : 'bg-primary-gradient'
                        } ${buttonDisabled ? 'opacity-50 pointer-events-none' : ''}`}
                    >
                        {isProcessing ? (
                            <Square className="h-3.5 w-3.5" fill="currentColor" />
                        ) : (
                            <ArrowUp className="h-4 w-4" />
                        )}
                    </Button>

                </div>

                <p className="text-xs text-muted-foreground mt-2 text-center">SQRL can make mistakes. Verify important data.</p>
            </div>
        </div>
    );
}