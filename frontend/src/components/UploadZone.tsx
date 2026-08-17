import { useRef, useState } from "react";
import { validateUploadFiles } from "../api/uploadValidation";

interface UploadZoneProps {
  disabled: boolean;
  onUpload: (files: File[]) => Promise<void>;
}

export default function UploadZone({ disabled, onUpload }: UploadZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");

  const submitFiles = async (files: File[]) => {
    if (disabled || files.length === 0) return;
    const validationError = validateUploadFiles(files);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError("");
    try {
      await onUpload(files);
    } catch {
      // 统一错误已由知识库状态显示，这里不重复弹出。
    }
  };

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.md"
        className="sr-only"
        aria-label="选择文档"
        onChange={(event) => {
          void submitFiles(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragEnter={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void submitFiles(Array.from(event.dataTransfer.files));
        }}
        className={
          "w-full border border-dashed px-3 py-3 text-left text-sm transition " +
          (dragging ? "border-blue-600 bg-blue-50" : "border-gray-300 bg-gray-50") +
          (disabled ? " cursor-not-allowed opacity-60" : " hover:border-blue-500")
        }
      >
        <span className="block font-medium text-gray-800">上传文档</span>
        <span className="mt-1 block text-xs leading-5 text-gray-500">
          拖放或点击选择 PDF、Word、TXT、MD，可批量上传
        </span>
      </button>
      {error && <p className="mt-1 text-xs text-red-700">{error}</p>}
    </div>
  );
}
