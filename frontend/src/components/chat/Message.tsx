import { Archive, FileText } from "lucide-react";
import type { ChatMessage, UploadedFile } from "@/types";
import AnalyticsCard from "@/components/visual/AnalyticsCard";

export const Message = ({
    message,
    onUpdateData,
}: {
    message: ChatMessage;
    onUpdateData: (data: unknown) => void;
}) => {
    const attachedFiles = (() => {
        const files = (message.data as { files?: UploadedFile[] } | null)?.files;
        return Array.isArray(files) ? files : [];
    })();
    const isFileMessage = message.type === "file" || attachedFiles.length > 0;

    // Assistant messages may carry a `body` with charts + summary
    const analysisBody = message.role === "assistant"
        ? (message.data as { body?: Record<string, unknown> } | null)?.body ?? null
        : null;

    const renderFileContent = (file?: UploadedFile) => {
        const fileName = file?.fileName || message.content || "Uploaded file";
        const extension = fileName.split(".").pop()?.toLowerCase();
        const isZip = extension === "zip";
        const isCsv = extension === "csv";

        return (
            <div className="flex items-center gap-3 min-w-0">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    {isZip ? <Archive className="h-5 w-5" /> : <FileText className="h-5 w-5" />}
                </div>
                <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-foreground">{fileName}</div>
                    <div className="text-xs text-muted-foreground">
                        {isZip ? "ZIP archive" : isCsv ? "CSV spreadsheet" : "Uploaded file"}
                    </div>
                </div>
            </div>
        );
    };

    const renderContent = () => {
        switch (message.type) {
            case "file":
                return attachedFiles.length > 0 ? (
                    <div className="flex flex-col gap-2">
                        {attachedFiles.map((file) => (
                            <div
                                key={`${file.fileName}-${file.fileUrl}`}
                                className="rounded-xl border border-border/70 bg-background/70 px-3 py-2 shadow-sm"
                            >
                                {renderFileContent(file)}
                            </div>
                        ))}
                    </div>
                ) : (
                    renderFileContent()
                );

            case "text":
            default:
                return (
                    <div>
                        {message.content && (
                            <div style={{ marginBottom: analysisBody ? 16 : 0 }}>
                                {message.content}
                            </div>
                        )}
                        {analysisBody && <AnalyticsCard body={analysisBody} />}
                    </div>
                );
        }
    };

    const hasMixedContent = Boolean(attachedFiles.length > 0 && message.content.trim());

    return (
        <div
            className={`flex items-start gap-3 ${
                message.role === "user" ? "justify-end" : "justify-start"
            }`}
        >
            {message.role === "assistant" && (
                <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-1">
                    <img src="/imgs/ai.png" alt="assistant" width={50} height={50} />
                </div>
            )}

            <div
                className={
                    message.role === "user"
                        ? isFileMessage
                            ? "max-w-[80%] rounded-2xl rounded-tr-sm border border-dashed border-primary/35 bg-primary/5 px-4 py-3 shadow-sm"
                            : "max-w-[80%] rounded-2xl rounded-tr-sm bg-primary-gradient px-4 py-3 text-white shadow-md"
                        : "max-w-[80%] rounded-2xl rounded-tl-sm border border-border bg-card px-4 py-3 shadow-sm"
                }
            >
                {hasMixedContent ? (
                    <div className="flex flex-col gap-3">
                        <div className="text-sm leading-6 text-foreground/90">{message.content}</div>
                        <div className="flex flex-col gap-2">
                            {attachedFiles.map((file) => (
                                <div
                                    key={`${file.fileName}-${file.fileUrl}`}
                                    className="rounded-xl border border-border/70 bg-background/70 px-3 py-2 shadow-sm"
                                >
                                    {renderFileContent(file)}
                                </div>
                            ))}
                        </div>
                    </div>
                ) : (
                    renderContent()
                )}
            </div>

            {message.role === "user" && (
                <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-1">
                    <img src="/imgs/user.png" alt="user" width={20} height={20} />
                </div>
            )}
        </div>
    );
};