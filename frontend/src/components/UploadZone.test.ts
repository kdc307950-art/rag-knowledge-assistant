import { describe, expect, it } from "vitest";
import { validateUploadFiles } from "../api/uploadValidation";

describe("validateUploadFiles", () => {
  it("rejects case-insensitive duplicate names in one batch", () => {
    const error = validateUploadFiles([
      new File(["a"], "Policy.TXT"),
      new File(["b"], "policy.txt"),
    ]);

    expect(error).toContain("同名文件");
  });
});
