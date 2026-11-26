# AGENTS.md — FinIntel Frontend (`frontend/`)

> Luật làm việc duy nhất cho mọi AI coding agent và lập trình viên trong thư mục `frontend/`.
> **BẮT BUỘC đọc trước khi viết bất kỳ dòng code UI nào:**
> 1. [`PRODUCT.md`](./PRODUCT.md) — định vị sản phẩm, persona, JTBD, từ vựng hiển thị, phạm vi MoSCoW.
> 2. [`DESIGN.md`](./DESIGN.md) — design tokens, luật định dạng số liệu VN, quy ước màu, danh sách CẤM chống giao diện kiểu AI, danh mục thành phần chuẩn.
> 3. [`docs/design/`](./docs/design/) — quyết định thiết kế (01), IA & wireframe (02), đối chiếu UI↔BE & hợp đồng API (03), playbook `ai-website-cloner-template` (04), kế hoạch kiểm thử & cổng chất lượng (05).

---

## 1. Tech Stack (kế thừa khung sạch từ `ai-website-cloner-template`)

- **Framework:** Next.js 16 (App Router, React 19, TypeScript strict — cấm `any`)
- **UI & Styling:** shadcn/ui (`@base-ui/react`) + Tailwind CSS v4 (ánh xạ token từ `DESIGN.md` vào `src/app/globals.css`)
- **Data & Tables:** TanStack Query (cache key gắn `snapshot_id`), TanStack Table, `nuqs` (URL state)
- **Charts:** Apache ECharts (`echarts`) với theme khóa theo `DESIGN.md`
- **API Contract & Mock:** `openapi-typescript` sinh type từ FastAPI `/openapi.json`; `msw` mock cho endpoint BE mới
- **Verification:** `vitest`, `@playwright/test`, `@axe-core/playwright`

## 2. Lệnh kiểm tra chuẩn

- `npm run dev` — Chạy dev server (proxy `/api/v1/*` → FastAPI local, không mở CORS bừa bãi)
- `npm run lint` — ESLint
- `npm run typecheck` — `tsc --noEmit`
- `npm run build` — Production build
- `npm run check` — `lint && typecheck && build` (bắt buộc PASS trước khi kết thúc mỗi đầu việc)
- `npm run test:unit` — Vitest (bắt buộc xanh cho `format.ts`, `narrative.ts`, CSV guard)
- `npm run test:e2e` — Playwright + axe-core + kiểm tra không tràn ngang

## 3. Vòng lặp xây dựng 4 bước (mượn kỷ luật kiểm chứng từ `clone-website`)

1. **Map & Contract:** đối chiếu màn hình với `docs/design/02-ia-flows-wireframes.md` và `03-ui-be-gap-and-api-contract.md`; khóa kiểu dữ liệu trước khi code.
2. **Build Clean:** dùng thành phần chuẩn trong `src/components/financial/` và `src/components/ui/`; đủ cả **4 trạng thái** (`loading` skeleton, `ready`, `empty`, `error` đã làm sạch).
3. **Verify Visually:** mở trình duyệt thật (Chrome DevTools / Playwright) tại `1366×768`, `1440×900` và `375×812`. Chụp ảnh kiểm chứng; xác minh dải Kết luận nằm trong màn hình đầu ở `1366×768`, 0 lỗi console, 0 tràn ngang, 0 lỗi axe nghiêm trọng.
4. **Critique & Repair:** rà soát theo danh sách CẤM trong `DESIGN.md §3` (và lệnh `critique`/`audit`/`polish` nếu cài Impeccable); sửa lỗi lớn nhất trước rồi kiểm chứng lại.

## 4. Quy tắc ranh giới cứng

- **KHÔNG sao chép** logo, hình ảnh, SVG thương hiệu, font có bản quyền, nội dung chữ hay mã nguồn từ `reference-lab/` hoặc bất kỳ website đối thủ nào vào `frontend/`.
- **KHÔNG lộ** thuật ngữ kỹ thuật backend (`REGIME_FALLBACK`, `snapshot_id`, `audit_quarantine`), đường dẫn đĩa, `.parquet`, hay traceback lên giao diện người dùng cuối.
- **KHÔNG tự nối chuỗi** định dạng số tài chính — mọi con số đi qua `src/lib/format.ts` (`vi-VN`, `tabular-nums`, ngoặc đơn cho số âm BCTC, dấu `−` U+2212 cho tỷ lệ, `–` cho 0, `N/A` cho thiếu dữ liệu).
- **KHÔNG sinh văn bản bằng LLM** — mọi câu diễn giải SHAP dùng quy tắc cố định trong `src/lib/narrative.ts`.

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
