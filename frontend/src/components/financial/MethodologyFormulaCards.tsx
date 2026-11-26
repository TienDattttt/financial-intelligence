import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function MethodologyFormulaCards() {
  return (
    <>
      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">Altman Z″ (Emerging Markets)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
          <p>
            Công thức điều chỉnh cho doanh nghiệp phi tài chính tại thị trường mới nổi: sử dụng vốn lưu động ròng, lợi nhuận giữ lại, EBIT và tỷ lệ VCSH / Tổng nợ phải trả.
          </p>
          <p className="text-xs text-ink">
            Ngưỡng phân vùng: <strong>&gt; 2,60</strong> An toàn · <strong>1,10–2,60</strong> Vùng xám · <strong>&lt; 1,10</strong> Nguy cơ kiệt quệ.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">
            Piotroski F-Score, Beneish M-Score &amp; Stress Test IMF WP/15/216
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
          <p>
            Piotroski F-Score đo 9 tiêu chí sức khỏe tài chính; Beneish M-Score sàng lọc biến dạng lợi nhuận; Core Earnings (Rouen, So &amp; Wang 2021) tách lợi nhuận cốt lõi; Stress Test mô phỏng tỷ trọng nợ rơi vào trạng thái khả năng trả lãi ICR &lt; 1,0x sau cú sốc.
          </p>
        </CardContent>
      </Card>

      <Card className="md:col-span-2">
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">
            Khoảng cách vỡ nợ cấu trúc thị trường — Merton Distance-to-Default (Bharath &amp; Shumway 2008)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-[13px] leading-5 text-ink-muted">
          <p>
            Đo khoảng cách từ giá trị tài sản ngụ ý thị trường đến ngưỡng nợ đến hạn{" "}
            <span className="font-mono text-ink">F = Nợ vay ngắn hạn + 0,5 × Nợ vay dài hạn</span>{" "}
            (bao gồm trái phiếu chuyển đổi theo chuẩn kế toán Việt Nam) trên cửa sổ trượt 252 phiên giao dịch thực tế và căn chỉnh nghiêm ngặt ngày công bố thực.
          </p>
          <p className="text-xs text-ink">
            Diễn giải: đơn vị tính bằng độ lệch chuẩn (<strong>σ</strong>);{" "}
            <strong>DTD càng thấp</strong> (đặc biệt <strong>&lt; 1,50 σ</strong> hoặc âm) phản ánh xác suất vỡ nợ ngụ ý thị trường{" "}
            <span className="font-mono">EDF = Φ(−DTD)</span> tăng mạnh. Chỉ báo hiển thị độc lập trên lớp phủ giá thị trường để đối chiếu chéo với điểm rủi ro BCTC cơ sở.
          </p>
        </CardContent>
      </Card>
    </>
  );
}
