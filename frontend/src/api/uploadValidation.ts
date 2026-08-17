export const ACCEPTED_EXTENSIONS = new Set(["pdf", "docx", "txt", "md"]);
export const MAX_FILE_BYTES = 20 * 1024 * 1024;
export const MAX_BATCH_BYTES = 200 * 1024 * 1024;

export function validateUploadFiles(files: File[]) {
  const duplicateNames = new Set<string>();
  const seenNames = new Set<string>();
  for (const file of files) {
    const normalizedName = file.name.trim().toLowerCase();
    if (seenNames.has(normalizedName)) duplicateNames.add(file.name);
    seenNames.add(normalizedName);
  }
  if (duplicateNames.size > 0) {
    return `本批不能选择同名文件：${[...duplicateNames].join("、")}`;
  }
  const unsupported = files.filter((file) => {
    const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
    return !ACCEPTED_EXTENSIONS.has(extension);
  });
  if (unsupported.length > 0) {
    return `不支持的文件格式：${unsupported.map((file) => file.name).join("、")}`;
  }
  const empty = files.filter((file) => file.size === 0);
  if (empty.length > 0) {
    return `空文件不能上传：${empty.map((file) => file.name).join("、")}`;
  }
  const oversized = files.filter((file) => file.size > MAX_FILE_BYTES);
  if (oversized.length > 0) {
    return `单个文件不能超过 20MB：${oversized.map((file) => file.name).join("、")}`;
  }
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  if (totalBytes > MAX_BATCH_BYTES) {
    return "本批文件合计不能超过 200MB";
  }
  return "";
}
