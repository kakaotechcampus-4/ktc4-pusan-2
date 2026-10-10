export function sha256(path: string): string;
export function verifyAssets(paths: {
  taskPath: string;
  manifestPath: string;
  runtimePackageJson: string;
}): string[];
