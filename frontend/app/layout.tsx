import type { Metadata } from "next";
import { Archivo } from "next/font/google";
import localFont from "next/font/local";
import "./globals.css";

// Self-hosted variable font (OFL, see fonts/BigShoulders-OFL.txt). Loaded locally so
// Next.js can compute fallback metrics from the file itself (Google's metric table lacks it).
const display = localFont({
  src: "./fonts/BigShoulders-Variable.woff2",
  variable: "--font-display-face",
  weight: "100 900",
  fallback: ["Impact", "sans-serif"],
});

const sans = Archivo({
  variable: "--font-sans-face",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Adaptive Lifting Coach",
  description: "A training block that adapts to what you actually lift.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} h-full antialiased`}>
      <body className="rubber min-h-full flex flex-col">{children}</body>
    </html>
  );
}
