export const APP_NAME = 'TriGuard';

export function displaySiteName(name: string): string {
  return name.replace(/\bZipfGuard\b/g, APP_NAME);
}
