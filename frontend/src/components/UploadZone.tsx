import { useRef, useState } from "react";
import type { UploadMetadata } from "../api/knowledge";
import { validateUploadFiles } from "../api/uploadValidation";

interface UploadZoneProps {
  disabled: boolean;
  onUpload: (files: File[], metadata: UploadMetadata) => Promise<void>;
}

export default function UploadZone({ disabled, onUpload }: UploadZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const [metadata, setMetadata] = useState<UploadMetadata>({
    classification: "other",
    department: "general",
    visibility: "all",
  });

  const submitFiles = async (files: File[]) => {
    if (disabled || files.length === 0) return;
    const validationError = validateUploadFiles(files);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError("");
    try {
      await onUpload(files, metadata);
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
      <div className="mb-2 grid grid-cols-2 gap-2">
        <label className="text-xs text-gray-600">
          分类
          <select
            value={metadata.classification}
            onChange={(event) => setMetadata((current) => ({ ...current, classification: event.target.value as UploadMetadata["classification"] }))}
            disabled={disabled}
            className="mt-1 w-full border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-800"
          >
            <option value="other">其他</option>
            <option value="policy">制度</option>
            <option value="process">流程</option>
            <option value="benefit">福利</option>
            <option value="technical">技术</option>
          </select>
        </label>
        <label className="text-xs text-gray-600">
          可见范围
          <select
            value={metadata.visibility}
            onChange={(event) => setMetadata((current) => ({ ...current, visibility: event.target.value as UploadMetadata["visibility"] }))}
            disabled={disabled}
            className="mt-1 w-full border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-800"
          >
            <option value="all">全员</option>
            <option value="department">本部门</option>
            <option value="private">仅上传者</option>
          </select>
        </label>
        <label className="col-span-2 text-xs text-gray-600">
          归属部门
          <input
            value={metadata.department}
            onChange={(event) => setMetadata((current) => ({ ...current, department: event.target.value }))}
            disabled={disabled}
            maxLength={64}
            pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
            className="mt-1 w-full border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-800"
          />
        </label>
      </div>
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
