export const APP_NAME = 'TriGuard';

export function displaySiteName(name: string): string {
  return name.replace(/\bZipfGuard\b/g, APP_NAME).replace('1000用户模拟站点','示例网站').replace('研究站点','示例网站');
}

export function displayRule(label: string): string {
  return label.replace('开发词表中的字典词', '常见字典词').replace('独立开发样本中的泄露口令', '已泄露口令库中的口令');
}
