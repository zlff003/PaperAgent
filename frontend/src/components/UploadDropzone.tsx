import { UploadCloud } from "lucide-react";
import { useRef, useState } from "react";

type Props = {
  onUpload: (file: File) => Promise<void>;
};

export function UploadDropzone({ onUpload }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  async function handleFile(file?: File) {
    if (!file) return;
    setBusy(true);
    try {
      await onUpload(file);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      className="upload-zone"
      type="button"
      disabled={busy}
      onClick={() => inputRef.current?.click()}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        void handleFile(event.dataTransfer.files[0]);
      }}
    >
      <UploadCloud size={22} />
      <span>{busy ? "上传中..." : "上传 PDF"}</span>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        hidden
        onChange={(event) => void handleFile(event.target.files?.[0])}
      />
    </button>
  );
}
