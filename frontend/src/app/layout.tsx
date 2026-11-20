import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, Source_Serif_4 } from "next/font/google";
import { AppShell } from "@/components/layout/AppShell";
import { AppProviders } from "@/components/providers/app-providers";
import "./globals.css";

const ibmPlexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600"],
});

const ibmPlexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500"],
});

const sourceSerif = Source_Serif_4({
  variable: "--font-source-serif",
  subsets: ["latin", "vietnamese"],
  weight: ["600"],
});

export const metadata: Metadata = {
  title: "FinIntel — Phân tích Rủi ro Tài chính & Kế toán Điều tra",
  description:
    "Nền tảng đánh giá sức khỏe tài chính, phát hiện dấu hiệu bất thường kế toán và mô phỏng kịch bản sốc cho doanh nghiệp niêm yết Việt Nam.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="vi"
      className={`${ibmPlexSans.variable} ${ibmPlexMono.variable} ${sourceSerif.variable} h-full`}
    >
      <body className="min-h-full">
        <AppProviders>
          <AppShell>{children}</AppShell>
        </AppProviders>
      </body>
    </html>
  );
}
