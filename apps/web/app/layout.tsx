import type { ReactNode } from "react";
import { Inter, Noto_Sans_Devanagari } from "next/font/google";
import TopNav from "@/components/TopNav";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const deva = Noto_Sans_Devanagari({ subsets: ["devanagari"], weight: ["400", "500", "600", "700"], variable: "--font-deva", display: "swap" });

export const metadata = { title: "Revenue Case Intelligence" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="mr" className={`${inter.variable} ${deva.variable}`}>
      <body>
        <TopNav />
        {children}
      </body>
    </html>
  );
}
