# ĐẶC TẢ DANH SÁCH CHỨC NĂNG HỆ THỐNG (MoSCoW PRIORITIZATION)

**Tên đề tài:** Xây dựng hệ thống tự động thu thập và phân tích báo cáo tài chính doanh nghiệp bằng Big Data và AI  
**Luồng nghiệp vụ cốt lõi:** Tự động lấy BCTC $\rightarrow$ Tính ROA, ROE, EPS, Debt Ratio, Cash Flow $\rightarrow$ Đánh giá doanh nghiệp  
**Công nghệ & Thuật toán trọng tâm:** Big Data ETL, Financial Ratio Analysis, PCA, Clustering, XGBoost, Anomaly Detection  

---

## 1. Ma trận Tổng quan Phạm vi Hệ thống (64 Chức năng)

| Nhóm ưu tiên | Vai trò trong dự án | Số lượng chức năng | Trọng tâm kỹ thuật |
| :--- | :--- | :---: | :--- |
| **1. MUST-HAVE**<br>*(Bắt buộc phải có)* | Đáp ứng trọn vẹn đề bài, tạo thành một phần mềm chạy xuyên suốt từ khâu tự động lấy BCTC đến khâu ra kết quả đánh giá doanh nghiệp bằng AI. | **32 chức năng** | Auth, CRUD Danh mục, Auto-Crawler & ETL, Tính 5 nhóm chỉ số cốt lõi, Pipeline 4 thuật toán AI, Dashboard đánh giá. |
| **2. SHOULD-HAVE**<br>*(Nên có)* | Hoàn thiện chiều sâu phân tích tài chính, giải thích được kết quả của AI (tránh "hộp đen") và cung cấp công cụ tra cứu tiện lợi cho người dùng. | **16 chức năng** | Explainable AI (SHAP), Phân rã DuPont, So sánh cùng cụm, Bộ lọc (Screener), Watchlist, Kiểm duyệt dữ liệu lỗi. |
| **3. COULD-HAVE**<br>*(Có thể mở rộng)* | Tăng độ chính xác cho mô hình AI bằng các chỉ số kiểm toán nâng cao và tự động hóa việc theo dõi biến động theo thời gian. | **9 chức năng** | Altman Z-Score / Beneish M-Score, Cảnh báo sớm tự động, Theo dõi dịch chuyển cụm qua các năm, Tự tạo công thức. |
| **4. WOULD-HAVE**<br>*(Đột phá / Sáng tạo)* | Những tính năng mới mẻ học hỏi từ các bài báo khoa học và nền tảng FinTech hiện đại để tạo điểm nhấn xuất sắc cho dự án. | **7 chức năng** | Mô phỏng kịch bản What-If với XGBoost, Kiểm định định luật Benford, AI tự động viết báo cáo nhận định & đối chiếu thuyết minh. |

---

## 2. Chi tiết Bộ Chức năng Hệ thống theo Mô hình MoSCoW

### I. MUST-HAVE (Bắt buộc phải có — 32 Chức năng Cốt lõi)
*Bộ khung tối thiểu bắt buộc phải hoàn thành để hệ thống vận hành xuyên suốt và đáp ứng đầy đủ các từ khóa kỹ thuật trong đề bài.*

#### Phân hệ 1: Quản lý Tài khoản & Phân quyền (Auth & RBAC)
1. **Đăng ký tài khoản:** Tạo tài khoản mới cho người dùng (Xác thực định dạng Email, mật khẩu mã hóa).
2. **Đăng nhập & Quản lý phiên (Session/JWT):** Đăng nhập hệ thống, duy trì trạng thái đăng nhập, đăng xuất.
3. **Quên mật khẩu & Đổi mật khẩu:** Khôi phục mật khẩu qua Email/OTP và cập nhật mật khẩu mới.
4. **Quản lý Hồ sơ cá nhân:** Xem và cập nhật thông tin tài khoản.
5. **Phân quyền theo vai trò (RBAC):** Phân tách rõ 2 luồng quyền hạn:
   - **Admin / Data Operator:** Quản lý dữ liệu gốc, cấu hình ETL, vận hành và huấn luyện mô hình AI.
   - **End-User / Analyst:** Tra cứu BCTC, phân tích chỉ số, xem kết quả đánh giá AI.

#### Phân hệ 2: Quản trị Dữ liệu Gốc (Master Data CRUD)
6. **CRUD Danh mục Ngành nghề & Sàn giao dịch:** Thêm, sửa, xóa, xem danh sách các nhóm ngành (Bất động sản, Sản xuất, Bán lẻ, Công nghệ...) và sàn niêm yết (HOSE, HNX, UPCOM).
7. **CRUD Hồ sơ Doanh nghiệp:** Thêm mới, sửa, xóa, tìm kiếm doanh nghiệp (Mã chứng khoán, Tên công ty, Mã số thuế, Ngành nghề, Số lượng cổ phiếu đang lưu hành — *dữ liệu bắt buộc để tính EPS*).
8. **CRUD Từ điển Khoản mục BCTC Chuẩn (Standard Chart of Accounts):** Quản lý danh sách các mã chỉ tiêu kế toán chuẩn của 3 bảng:
   - Bảng cân đối kế toán (*Balance Sheet*)
   - Báo cáo kết quả hoạt động kinh doanh (*Income Statement*)
   - Báo cáo lưu chuyển tiền tệ (*Cash Flow Statement*)

#### Phân hệ 3: Tự động Thu thập BCTC & Đường ống Big Data ETL
9. **Cấu hình Nguồn & Lập lịch Tự động Lấy BCTC (Auto-Crawler Scheduler):** Cài đặt nguồn dữ liệu (API tài chính / Web Scraping cổng công bố thông tin), thiết lập lịch chạy tự động định kỳ (theo Quý/Năm) hoặc bấm nút thu thập ngay theo mã doanh nghiệp.
10. **Tiến trình Extract (Bóc tách dữ liệu thô):** Tự động tải và đọc cấu trúc BCTC từ nguồn bên ngoài đưa vào vùng lưu trữ tạm (*Staging / Raw Zone*).
11. **Tiến trình Transform (Chuẩn hóa & Làm sạch dữ liệu):**
    - Tự động ánh xạ (*Mapping*) tên các dòng kế toán khác nhau từ nguồn thu thập về **Mã khoản mục BCTC chuẩn** của hệ thống.
    - Đồng nhất đơn vị tiền tệ (quy đổi toàn bộ về đơn vị chuẩn) và xử lý các giá trị khuyết thiếu (`Null`/`NaN` $\rightarrow$ `0` hoặc nội suy).
12. **Kiểm tra Cân bằng Kế toán Tự động (Automated Accounting Validation):** Tự động kiểm tra tính đúng đắn số học trước khi nạp vào kho:
    $$\text{Tổng Tài sản} = \text{Nợ phải trả} + \text{Vốn chủ sở hữu}$$
    $$\text{Lợi nhuận gộp} = \text{Doanh thu thuần} - \text{Giá vốn hàng bán}$$
13. **Tiến trình Load & Lưu trữ Big Data:** Ghi dữ liệu BCTC sạch vào cơ sở dữ liệu phân tích theo cấu trúc chuỗi thời gian (Mã doanh nghiệp + Năm + Quý + Phiên bản báo cáo).
14. **CRUD Báo cáo Tài chính & Giám sát ETL:** Xem danh sách BCTC đã thu thập, xem lịch sử chạy Job ETL (Thành công / Thất bại / Log lỗi), cho phép Admin sửa trực tiếp con số bị lệch hoặc xóa/nạp lại BCTC.

#### Phân hệ 4: Động cơ Tính toán Chỉ số Tài chính (Financial Ratio Analysis Engine)
15. **Tự động tính nhóm Sinh lời (`ROA`, `ROE`):**
    $$\text{ROA} = \frac{\text{Lợi nhuận sau thuế (TTM)}}{\text{Tổng tài sản bình quân}}$$
    $$\text{ROE} = \frac{\text{Lợi nhuận sau thuế (TTM)}}{\text{Vốn chủ sở hữu bình quân}}$$
    *(Kèm các chỉ số bổ trợ: Biên lợi nhuận gộp, Biên lợi nhuận ròng - ROS).*
16. **Tự động tính nhóm Cổ phiếu (`EPS`):**
    $$\text{EPS cơ bản} = \frac{\text{Lợi nhuận sau thuế thuộc về cổ đông công ty mẹ}}{\text{Số lượng cổ phiếu lưu hành bình quân}}$$
17. **Tự động tính nhóm Đòn bẩy & Nợ (`Debt Ratio`):**
    $$\text{Debt-to-Assets Ratio} = \frac{\text{Tổng Nợ phải trả}}{\text{Tổng Tài sản}}$$
    $$\text{Debt-to-Equity (D/E)} = \frac{\text{Tổng Nợ vay tài chính}}{\text{Vốn chủ sở hữu}}$$
    *(Kèm chỉ số Thanh toán hiện hành - Current Ratio và Thanh toán nhanh - Quick Ratio).*
18. **Tự động tính nhóm Dòng tiền (`Cash Flow Analysis`):**
    - Lưu chuyển tiền tệ thuần từ hoạt động kinh doanh (*Operating Cash Flow - OCF*).
    - Dòng tiền tự do: $\text{Free Cash Flow (FCF)} = \text{OCF} - \text{CAPEX}$.
    - Tỷ số Chất lượng dòng tiền: $\frac{\text{OCF}}{\text{Lợi nhuận sau thuế}}$ và $\frac{\text{OCF}}{\text{Tổng nợ phải trả}}$.
19. **Tổng hợp Chuỗi thời gian & Trung bình ngành:** Tự động tính số liệu trượt 4 quý gần nhất (**TTM**), tốc độ tăng trưởng cùng kỳ năm trước (**YoY**), quý trước (**QoQ**) và **Chỉ số trung bình ngành** tại từng kỳ.
20. **Xử lý Ngoại lệ Toán học:** Tự động xử lý các trường hợp mẫu số bằng $0$ hoặc Vốn chủ sở hữu âm (đánh dấu cảnh báo thay vì ra số dương giả tạo khi cả tử số và mẫu số đều âm).

#### Phân hệ 5: Đường ống AI Phân tích & Đánh giá Doanh nghiệp (AI Core Pipeline)
21. **Tiền xử lý & Chuẩn hóa Đặc trưng (Feature Scaling):** Tự động xử lý ngoại lai cực đoan và chuẩn hóa bộ chỉ số tài chính bằng `RobustScaler` / `StandardScaler` trước khi đưa vào mô hình học máy.
22. **Mô-đun Giảm chiều dữ liệu (`PCA` - Principal Component Analysis):**
    - Nén bộ chỉ số tài chính đa chiều (15–30 chỉ số) xuống các Thành phần chính độc lập tuyến tính ($PC_1, PC_2, PC_3, \dots$) giữ lại tối thiểu $80\% - 85\%$ phương sai thông tin, loại bỏ hiện tượng đa cộng tuyến giữa các chỉ số cùng nhóm.
23. **Mô-đun Phân cụm Doanh nghiệp (`Clustering`):**
    - Chạy thuật toán phân cụm (**K-Means** hoặc **Hierarchical Clustering**) trên các tọa độ thành phần chính từ PCA để tự động gom các doanh nghiệp có cấu trúc tài chính giống nhau vào cùng một cụm.
    - Tự động tính toán giá trị trung tâm của từng cụm (*Cluster Centroids*) để định danh đặc điểm cụm (ví dụ: *Cụm 1: Sinh lời cao - Dòng tiền mạnh - Nợ thấp*; *Cụm 2: Đòn bẩy nợ cao - Dòng tiền âm*).
24. **Mô-đun Phát hiện Bất thường (`Anomaly Detection`):**
    - Áp dụng thuật toán học không giám sát (**Isolation Forest** hoặc **Local Outlier Factor - LOF**) trên không gian chỉ số để phát hiện các BCTC có sự lệch pha bất thường.
    - Xuất ra **Điểm bất thường (Anomaly Score)** và **Danh sách Cờ đỏ (Red Flags)** cho từng doanh nghiệp (ví dụ: Doanh nghiệp có ROE và EPS tăng đột biến nhưng Dòng tiền từ hoạt động kinh doanh âm nặng nhiều kỳ liên tiếp).
25. **Mô-đun Dự báo & Chấm điểm Đánh giá (`XGBoost`):**
    - Sử dụng mô hình học có giám sát **XGBoost** nhận đầu vào gồm: *Bộ chỉ số tài chính (ROA, ROE, EPS, Debt Ratio, Cash Flow...) + Đặc trưng PCA/Cụm + Điểm Anomaly Score*.
    - Đầu ra: Dự báo **Xác suất Rủi ro / Kiệt quệ Tài chính (Financial Distress Probability)**, quy đổi ra **Điểm Sức khỏe Tài chính ($0 - 100$ điểm)** và phân loại hạng doanh nghiệp (**Hạng A: Tốt/An toàn, Hạng B: Khá, Hạng C: Cần lưu ý, Hạng D: Rủi ro cao**).
26. **Quản lý Kích hoạt Pipeline AI:** Chức năng cho phép Admin kích hoạt chạy toàn bộ luồng `PCA` $\rightarrow$ `Clustering` $\rightarrow$ `Anomaly Detection` $\rightarrow$ `XGBoost` khi có dữ liệu BCTC kỳ mới và lưu kết quả đánh giá vào Database.

#### Phân hệ 6: Giao diện Tra cứu, Trực quan hóa & Đánh giá Doanh nghiệp (User Workspace)
27. **Tìm kiếm & Bảng Xếp hạng Tổng quan:** Tìm nhanh doanh nghiệp theo Mã CK/Tên; Bảng tổng hợp xếp hạng các doanh nghiệp trên thị trường theo Điểm đánh giá AI và các chỉ số ROA, ROE, EPS, Debt Ratio, Cash Flow.
28. **Trình xem Báo cáo Tài chính Chuẩn hóa:** Hiển thị chi tiết 3 bảng BCTC của doanh nghiệp theo Quý và Năm.
29. **Biểu đồ Xu hướng Chỉ số Tài chính:** Trực quan hóa biến động của ROA, ROE, EPS, Debt Ratio và Cash Flow qua các kỳ bằng biểu đồ đường/cột kết hợp đường trung bình ngành.
30. **Thẻ Kết quả Đánh giá Doanh nghiệp bằng AI (AI Scorecard):** Hiển thị trực quan Hạng doanh nghiệp (A/B/C/D), Điểm sức khỏe từ XGBoost, Nhãn Cụm tài chính hiện tại và trạng thái Cảnh báo bất thường.
31. **Biểu đồ Trực quan Không gian Phân cụm (PCA & Clustering Chart):** Biểu đồ phân tán 2D/3D hiển thị vị trí của doanh nghiệp đang xem nằm ở đâu trong cụm của nó và khoảng cách tới các cụm khác.
32. **Chi tiết Cảnh báo Dị biệt (Anomaly Breakdown View):** Liệt kê cụ thể chỉ số nào của doanh nghiệp bị thuật toán Anomaly Detection đánh dấu là bất thường trong kỳ BCTC.

---

### II. SHOULD-HAVE (Nên có — 16 Chức năng Nâng cao Giá trị Sử dụng & Học thuật)
*Giải quyết bài toán giải thích quyết định của AI (Explainable AI) và cung cấp bộ công cụ phân tích chuyên sâu cho người dùng.*

#### 1. Giải thích Quyết định của AI & Phân tích Chuyên sâu
33. **Giải thích Mô hình XGBoost bằng SHAP Values (Explainable AI - XAI):** Hiển thị biểu đồ đóng góp của từng yếu tố (ví dụ: *ROE cao giúp cộng $+15$ điểm, nhưng Debt Ratio vượt ngưỡng làm trừ $-22$ điểm*) để minh bạch hóa phán quyết của XGBoost.
34. **Phân tích Cây DuPont Tương tác (3 bước & 5 bước):** Tự động bóc tách:
    $$\text{ROE} = \text{Biên lợi nhuận ròng (ROS)} \times \text{Vòng quay tổng tài sản} \times \text{Đòn bẩy tài chính}$$
    Kết nối trực tiếp 3 chỉ số trong đề bài là **ROA, ROE và Debt Ratio** vào chung một mô hình nhân quả.
35. **Biểu đồ Radar 5 Trụ cột Tài chính:** Vẽ biểu đồ mạng nhện 5 cạnh (*ROA - ROE - EPS - An toàn Nợ (Debt Ratio) - Sức mạnh Dòng tiền (Cash Flow)*) của doanh nghiệp đè lên biểu đồ trung bình của **Cụm (Cluster)**.
36. **Chế độ Xem BCTC Theo Tỷ trọng (Common-Size Analysis):** Chuyển đổi bảng BCTC từ số tiền tuyệt đối sang tỷ lệ $\%$ (từng khoản mục chia cho Tổng Tài sản hoặc Tổng Doanh thu).
37. **Truy vết Công thức Chỉ số (Formula Drill-down):** Khi nhấp vào con số ROA, ROE hay Debt Ratio trên màn hình, hệ thống hiển thị công thức toán học và các con số gốc từ BCTC đã dùng để tính ra kết quả đó.

#### 2. Công cụ Sàng lọc, So sánh & Cá nhân hóa cho Người dùng
38. **Bộ lọc Doanh nghiệp Đa tiêu chí (AI & Ratio Stock Screener):** Lọc danh sách doanh nghiệp kết hợp điều kiện chỉ số (ví dụ: $\text{ROE} > 15\%$, $\text{Debt Ratio} < 50\%$, $\text{Cash Flow HĐKD} > 0$) và điều kiện AI (*Thuộc Cụm Tăng trưởng*, *Hạng XGBoost = A*, *Không có Anomaly*).
39. **So sánh Doanh nghiệp Ngang hàng (Peer Comparison):** Chọn 2–5 doanh nghiệp (hoặc tự động lấy các doanh nghiệp gần nhất trong cùng cụm PCA/Clustering) đặt cạnh nhau để đối chiếu toàn bộ chỉ số và điểm AI.
40. **CRUD Danh mục Theo dõi (Watchlist) & Ghi chú:** Người dùng tự tạo danh sách các doanh nghiệp quan tâm và lưu ghi chú phân tích cá nhân.
41. **Xuất Báo cáo Đánh giá (Export Excel / PDF):** Kết xuất bảng số liệu BCTC, bộ chỉ số và phiếu đánh giá AI của doanh nghiệp ra file Excel hoặc PDF chuẩn.

#### 3. Quản trị Chất lượng Dữ liệu & Huấn luyện Mô hình (Back-office)
42. **Công cụ Upload BCTC Bổ sung (Excel/CSV/PDF Parser):** Cho phép Admin tải lên file BCTC của các doanh nghiệp chưa thu thập tự động được để hệ thống bóc tách vào kho dữ liệu.
43. **Hàng đợi Kiểm duyệt Dữ liệu Lỗi (Data QA Queue):** Giao diện gom toàn bộ các BCTC bị lỗi cân bằng kế toán trong quá trình ETL để Admin kiểm tra, sửa nhanh và bấm duyệt (`Publish`).
44. **Quản lý Gán nhãn Dữ liệu Huấn luyện cho XGBoost (Ground-Truth Labeling):** Giao diện để Admin cấu hình quy tắc gán nhãn lịch sử cho mô hình XGBoost học (ví dụ: Gán nhãn `1 - Rủi ro` cho các doanh nghiệp từng bị cảnh báo/kiểm soát đặc biệt, lỗ 2 năm liên tiếp hoặc mất khả năng thanh toán).
45. **Bảng Đo lường Chất lượng Mô hình AI (Model Evaluation Metrics Dashboard):** Hiển thị các thước đo học thuật của mô hình ngay trên trang quản trị:
    - **PCA:** Biểu đồ *Scree Plot* (Tỷ lệ phương sai tích lũy theo số thành phần chính).
    - **Clustering:** Chỉ số *Silhouette Score* và *Elbow Method* chứng minh số cụm $K$ tối ưu.
    - **XGBoost:** Độ chính xác *Accuracy, Precision, Recall, F1-Score, ROC-AUC* và Ma trận nhầm lẫn (*Confusion Matrix*).
46. **Cấu hình Siêu tham số & Huấn luyện lại (Model Retraining UI):** Cho phép Admin tùy chỉnh tham số (số thành phần PCA, số cụm $K$, ngưỡng cắt Anomaly) và bấm nút huấn luyện lại mô hình.
47. **Quản lý Phiên bản BCTC (Tự lập vs. Kiểm toán):** Lưu trữ và phân biệt BCTC doanh nghiệp tự lập hàng quý với BCTC đã được kiểm toán/soát xét để đánh giá độ lệch số liệu sau kiểm toán.
48. **Xử lý Mất cân bằng Dữ liệu (SMOTE / Class Weighting):** Tích hợp bước cân bằng dữ liệu tự động trong pipeline trước khi huấn luyện XGBoost.

---

### III. COULD-HAVE (Có thể mở rộng — 9 Chức năng Gia tăng Sức mạnh Dự báo)
*Mở rộng độ sâu nghiệp vụ tài chính và tự động hóa cảnh báo khi có thêm thời gian phát triển.*

49. **Tích hợp Bộ Chỉ số Kiểm toán Kinh điển làm Biến đầu vào (Engineered Domain Features):** Tự động tính thêm **Altman Z-Score** (đo nguy cơ phá sản), **Beneish M-Score** (8 chỉ số phát hiện làm đẹp lợi nhuận) và **Piotroski F-Score** (9 tiêu chí sức khỏe tài chính) làm đặc trưng đầu vào cho **XGBoost** và **Anomaly Detection**.
50. **Theo dõi Quỹ đạo Dịch chuyển Cụm (Cluster Migration Tracker):** Trực quan hóa sự di chuyển của một doanh nghiệp từ cụm này sang cụm khác qua các năm (ví dụ: Năm 2023 ở *Cụm An toàn* $\rightarrow$ Năm 2025 dịch chuyển sang *Cụm Rủi ro Đòn bẩy cao*).
51. **Hệ thống Cảnh báo Sớm Tự động (Automated Early Warning Alerts):** Tự động gửi thông báo (Email / Web Notification) cho người dùng khi doanh nghiệp trong Watchlist vừa có BCTC mới mà bị mô hình Anomaly Detection phát hiện bất thường hoặc bị XGBoost hạ bậc xếp hạng.
52. **Trình Tạo Công thức Chỉ số Tùy chỉnh (Custom Ratio Formula Builder):** Cho phép nhà phân tích tự ghép các khoản mục BCTC để tạo thêm chỉ số tài chính mới ngoài bộ chỉ số mặc định.
53. **Chuẩn hóa Đặc thù Ngành (Sector-Adjusted Z-Score):** Tự động quy đổi ROA, ROE, Debt Ratio của doanh nghiệp thành điểm Z-score so với trung bình ngành của chính doanh nghiệp đó trước khi đưa vào PCA.
54. **Biểu đồ Dòng chảy Tài chính (Financial Sankey Diagram):** Vẽ sơ đồ dòng chảy từ Doanh thu $\rightarrow$ Giá vốn & Các loại chi phí $\rightarrow$ Lợi nhuận ròng và Dòng tiền.
55. **Cơ chế Phản hồi Chuyên gia (Human-in-the-Loop Feedback):** Cho phép chuyên gia phân tích xác nhận một cảnh báo của Anomaly Detection là *"Cảnh báo đúng"* hay *"Báo động giả do đặc thù kinh doanh mùa vụ"* để cải thiện mô hình.
56. **Lịch sử Nhật ký Hệ thống (Audit Logs):** Lưu vết toàn bộ lịch sử chỉnh sửa BCTC và lịch sử chạy ETL/AI.
57. **Cung cấp RESTful API Trích xuất Dữ liệu:** Cho phép xuất dữ liệu chỉ số và điểm đánh giá AI qua API để kết nối với các công cụ BI bên ngoài.

---

### IV. WOULD-HAVE / INNOVATION (Đột phá & Sáng tạo — 7 Chức năng Học hỏi từ Bài báo Khoa học & FinTech)
*Các tính năng tạo điểm nhấn khác biệt (Unique Selling Points) được đúc kết từ các công trình nghiên cứu quốc tế (IEEE, Springer, MDPI) và các nền tảng tài chính thế hệ mới.*

58. **Công cụ Mô phỏng Kịch bản "What-If" Tương tác Thời gian thực với XGBoost (Interactive AI Stress-Testing):**
    - *Cơ chế:* Người dùng kéo thanh trượt giả định biến động (ví dụ: *"Nếu kỳ tới Doanh thu giảm $15\%$, Debt Ratio tăng thêm $20\%$ và Cash Flow giảm $30\%$ thì sao?"*). Hệ thống lập tức tính lại ROA, ROE, EPS, chiếu lại vào không gian PCA và chạy mô hình **XGBoost** theo thời gian thực để cho biết Điểm sức khỏe doanh nghiệp sẽ thay đổi như thế nào.
59. **Kiểm định Gian lận Số học bằng Định luật Benford kết hợp Anomaly Detection (Benford's Law Forensic Scanner):**
    - *Cơ chế:* Kiểm tra tần suất xuất hiện của chữ số đầu tiên (từ $1$ đến $9$) của mọi con số trên BCTC qua kiểm định $\chi^2$ (Chi-square) theo Định luật Benford, kết hợp với **Isolation Forest (Anomaly Detection)** để phát hiện các BCTC có dấu hiệu bị con người can thiệp hoặc làm tròn số liệu nhân tạo.
60. **Tự động Sinh Báo cáo Nhận định Tài chính bằng AI (AI-Generated Executive Commentary):**
    - *Cơ chế:* Tự động tổng hợp kết quả đầu ra của 4 thuật toán (`PCA`, `Clustering`, `Anomaly Detection`, `XGBoost`) cùng 5 nhóm chỉ số (`ROA`, `ROE`, `EPS`, `Debt Ratio`, `Cash Flow`) thành một bản nhận định hoàn chỉnh bằng ngôn ngữ tự nhiên nêu rõ điểm mạnh, điểm yếu và cảnh báo rủi ro trọng yếu.
61. **Liên kết Điểm Bất thường với Văn bản Thuyết minh BCTC (Anomaly-to-Footnote Linker):**
    - *Cơ chế:* Khi mô hình **Anomaly Detection** báo đỏ một chỉ số (như Nợ vay hoặc Khoản phải thu tăng đột biến), hệ thống tự động trích xuất và hiển thị đúng đoạn văn bản trong phần **Thuyết minh Báo cáo tài chính** của file BCTC gốc để giải thích chi tiết khoản mục đó.
62. **Kiểm thử Lịch sử Hiệu quả Đánh giá của AI (AI Evaluation Backtesting Engine):**
    - *Cơ chế:* Cho phép kiểm chứng trên dữ liệu quá khứ (ví dụ: lùi về 3 năm trước) để đối chiếu xem nhóm doanh nghiệp được **XGBoost** chấm hạng A và không bị **Anomaly Detection** gắn cờ có kết quả kinh doanh thực tế trong các năm tiếp theo vượt trội như thế nào so với nhóm bị chấm hạng D.
63. **Tự động Bóc tách "Lợi nhuận Cốt lõi" trước khi tính ROA, ROE, EPS (Core Earnings Normalizer):**
    - *Cơ chế:* Tự động loại bỏ các khoản lợi nhuận đột biến một lần (như bán thanh lý tài sản, đánh giá lại khoản đầu tư tài chính) ra khỏi Lợi nhuận sau thuế để tính **Core ROA, Core ROE, Core EPS**, giúp mô hình PCA và XGBoost không bị nhiễu bởi lợi nhuận ảo.
64. **Đặc trưng Chuỗi Thời gian - Không gian Đa chiều (Temporal-Spatial Feature Engineering):**
    - *Cơ chế:* Tự động tính **Vector vận tốc thay đổi chỉ số qua 4 quý** (*Temporal*) kết hợp với **Khoảng cách Mahalanobis từ doanh nghiệp tới tâm cụm PCA** (*Spatial*) làm biến đầu vào cho XGBoost, giúp phát hiện sớm xu hướng suy thoái trước khi doanh nghiệp thực sự báo lỗ.

---

## 3. Khuyến nghị Tổ hợp Triển khai Tối ưu

Để dự án vừa vững chắc về mặt kỹ thuật phần mềm vừa nổi bật về hàm lượng nghiên cứu Big Data & AI, lộ trình thực hiện được khuyến nghị như sau:

1. **Triển khai 100% nhóm MUST-HAVE (Chức năng 1 $\rightarrow$ 32):** Đảm bảo hoàn thành trọn vẹn luồng nghiệp vụ khép kín từ tự động thu thập BCTC (ETL) đến tính toán chỉ số và đánh giá bằng bộ 4 thuật toán AI.
2. **Chọn lọc 8–10 chức năng trọng tâm của nhóm SHOULD-HAVE:** Ưu tiên **Giải thích mô hình bằng SHAP (33)**, **Phân tích cây DuPont (34)**, **Biểu đồ Radar 5 trụ cột (35)**, **Bộ lọc đa tiêu chí (38)** và **Bảng đo lường chất lượng mô hình AI (45)**.
3. **Chọn 2–3 tính năng đột phá trong nhóm COULD-HAVE & WOULD-HAVE làm điểm nhấn:** Khuyến nghị chọn **Tích hợp Altman Z-Score / Beneish M-Score (49)** + **Mô phỏng kịch bản What-If trực tiếp trên XGBoost (58)** + **Tự động sinh bản nhận định tài chính từ kết quả của 4 mô hình AI (60)**.