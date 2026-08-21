import type { Metadata } from "next";
import "@/styles/globals.css";
import { Sidebar } from "@/components/shared/sidebar";

export const metadata: Metadata = {
  title: "TradeMind",
  description: "AI-powered trading platform for Indian stock markets",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-zinc-950 text-zinc-100 antialiased">
        <Sidebar />
        <main className="ml-56 min-h-screen p-6">{children}</main>
      </body>
    </html>
  );
}
