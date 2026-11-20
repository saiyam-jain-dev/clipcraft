import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ClipCraft — Your personal clipping studio",
  description: "ClipCraft is your personal studio for turning long videos into captioned vertical clips.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
