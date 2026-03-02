import { ChangeEvent, ClipboardEvent, DragEvent, FormEvent, KeyboardEvent, useRef } from "react";
import { Paperclip, Send, Square, X } from "lucide-react";
import { APP_CONFIG } from "@/config/app";
import { cn } from "@/lib/utils";
import { AttachedImage } from "@/types/chat";
import { compressImage } from "@/utils/imageUtils";

const MAX_IMAGES_PER_MESSAGE = 9;

interface ChatComposerProps {
  value: string;
  pendingImages: AttachedImage[];
  disabled: boolean;
  isResponding: boolean;
  onChange: (value: string) => void;
  onImagesChange: (imgs: AttachedImage[]) => void;
  onSubmit: () => void;
  onAbort: () => void;
}

export function ChatComposer({
  value,
  pendingImages,
  disabled,
  isResponding,
  onChange,
  onImagesChange,
  onSubmit,
  onAbort
}: ChatComposerProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const canSend = (value.trim().length > 0 || pendingImages.length > 0) && !disabled;

  const handleImageFiles = async (incoming: File[]) => {
    if (!incoming.length || disabled) {
      return;
    }
    const imageFiles = incoming.filter((file) => file.type.startsWith("image/"));
    if (!imageFiles.length) {
      return;
    }

    const slots = Math.max(0, MAX_IMAGES_PER_MESSAGE - pendingImages.length);
    if (slots <= 0) {
      return;
    }
    const accepted = imageFiles.slice(0, slots);
    const results = await Promise.allSettled(accepted.map((file) => compressImage(file)));
    const compressed = results
      .filter((r): r is PromiseFulfilledResult<AttachedImage> => r.status === "fulfilled")
      .map((r) => r.value);
    if (compressed.length > 0) {
      onImagesChange([...pendingImages, ...compressed]);
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (canSend) {
      onSubmit();
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSend) {
        onSubmit();
      }
    }
  };

  const handleFileSelect = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    await handleImageFiles(files);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handlePaste = async (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.files || []).filter((file) =>
      file.type.startsWith("image/")
    );
    if (!files.length) {
      return;
    }
    event.preventDefault();
    await handleImageFiles(files);
  };

  const handleDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files || []);
    await handleImageFiles(files);
  };

  return (
    <div
      className="z-20 w-full bg-gradient-to-t from-app-bg via-app-bg/95 to-transparent p-6"
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
    >
      <form onSubmit={handleSubmit} className="group relative mx-auto w-full max-w-4xl">
        <div className="pointer-events-none absolute -inset-0.5 rounded-xl bg-gradient-to-r from-app-info/50 to-app-accentAlt/50 opacity-20 blur transition duration-500 group-hover:opacity-50" />
        {pendingImages.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2 rounded-xl border border-app-border/30 bg-app-surface/75 p-2">
            {pendingImages.map((img, idx) => (
              <div key={`${img.name}_${idx}`} className="relative h-16 w-16 overflow-hidden rounded-md border border-app-border/35">
                <img src={img.dataUrl} alt={img.name} className="h-full w-full object-cover" />
                <button
                  type="button"
                  onClick={() => onImagesChange(pendingImages.filter((_, i) => i !== idx))}
                  className="absolute right-0 top-0 rounded-bl bg-app-bg/70 p-0.5 text-app-text hover:bg-app-bg"
                  title="移除图片"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="relative flex items-center overflow-hidden rounded-xl border border-app-border/40 bg-app-surface/90 backdrop-blur-xl transition-all duration-300 focus-within:border-app-info/70 focus-within:shadow-[var(--app-shadow-composer-focus)]">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            onChange={(event) => {
              void handleFileSelect(event);
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || pendingImages.length >= MAX_IMAGES_PER_MESSAGE}
            className={cn(
              "ml-2 rounded-lg p-2 transition-colors",
              disabled || pendingImages.length >= MAX_IMAGES_PER_MESSAGE
                ? "cursor-not-allowed text-app-muted/35"
                : "text-app-muted/80 hover:text-app-info"
            )}
            title="上传图片"
          >
            <Paperclip size={18} />
          </button>
          <textarea
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={(event) => {
              void handlePaste(event);
            }}
            placeholder={APP_CONFIG.text.inputPlaceholder}
            rows={2}
            className="max-h-40 min-h-12 w-full resize-y bg-transparent px-3 py-3 text-sm text-app-text outline-none placeholder:text-app-muted/30"
          />
          <button
            type={isResponding ? "button" : "submit"}
            onClick={isResponding ? onAbort : undefined}
            disabled={isResponding ? false : !canSend}
            className={cn(
              "mr-2 rounded-lg p-3 transition-all focus:outline-none",
              isResponding
                ? "bg-app-accentAlt/25 text-app-accentAlt hover:bg-app-accentAlt/40"
                : canSend
                ? "bg-app-accent/20 text-app-accentAlt hover:bg-app-accent/40"
                : "bg-app-accent/20 text-app-muted/60 opacity-30"
            )}
          >
            {isResponding ? <Square size={18} /> : <Send size={18} className={canSend ? "animate-pulse" : ""} />}
          </button>
        </div>
        <div className="mt-3 text-center font-mono text-[10px] uppercase tracking-widest text-app-muted/40">
          {APP_CONFIG.text.securityHint}
        </div>
      </form>
    </div>
  );
}
