import type { Metadata } from "next";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import { AppShell } from "@/components/layout/AppShell";
import { AppProviders } from "@/components/providers/app-providers";
import "./globals.css";

const THEME_INIT_SCRIPT = `(function(){try{var k='finintel:theme:v1';var p=localStorage.getItem(k);var m=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches;var d=p==='dark'||(p==='system'&&m);var r=document.documentElement;if(d){r.classList.add('dark');r.setAttribute('data-theme','dark');r.style.colorScheme='dark';}else{r.classList.remove('dark');r.setAttribute('data-theme','light');r.style.colorScheme='light';}}catch(e){}})();`;

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
    <html lang="vi" className="h-full" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-full">
        <AppProviders>
          <AppShell>{children}</AppShell>
        </AppProviders>
      </body>
    </html>
  );
}
