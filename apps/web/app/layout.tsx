import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "SafeOps · 运营控制台", description: "支持持久化执行、人工审批和对账的智能体运营控制台" };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
