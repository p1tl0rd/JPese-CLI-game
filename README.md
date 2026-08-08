# Kana Rush

Game terminal học **46 Hiragana cơ bản** dành cho người Việt, xây dựng trên
active recall, spaced repetition, corrective feedback, interleaving và
successive relearning.

## Tính năng

- **46 Hiragana** kèm romaji chuẩn (Hepburn) và biệt danh hợp lệ:
  `shi/si`, `chi/ti`, `tsu/tu`, `fu/hu`, `wo/o`, `n/nn`
- **5 trạng thái thẻ**: NEW → LEARNING → REVIEW → MASTERED (RELEARNING khi trả lời sai)
- **Buổi học (Learn) 6 giai đoạn**: mã hoá → gợi nhớ lần đầu (thang gợi ý 4 bậc
  `?`) → gợi nhớ lẫn nhau → nhầm lẫn (comparison) → Boss Round (HP/damage
  scale theo level XP và số kana mastered, damage theo tốc độ)
- **Review** (Quick/Full) theo nhóm 60/20/10/10 (đến hạn / yếu / nhầm / vững),
  kèm câu hỏi ngược (romaji → kana), chuỗi kana, từ ngắn, so sánh kana dễ nhầm
- **Word Bridge**: mở khoá từ khi đã học hết các kana trong từ
- **Speed Run**: 60 giây, mở khoá khi có ≥10 kana REVIEW/MASTERED + độ chính xác ≥85%
- **Chẩn đoán lần đầu** (diagnostic): 46 kana theo khối 10, không phản hồi từng câu
- **Lịch trình minh bạch**: khoảng cách 0p/8h/1d/3d/7d/14d/30d, có thể xem trong game
- **Lưu tiến trình an toàn**: ghi atomically + backup + tự phục hồi khi hỏng

## Cài đặt

Yêu cầu Python **≥ 3.11**.

### Windows

```powershell
cd JPese-CLI-game
python -m pip install -r requirements.txt
```

### Ubuntu

```bash
cd JPese-CLI-game
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Chạy game

```powershell
.\run.bat         # Windows (tự dùng .venv nếu có)
./run.sh          # Ubuntu (nhớ chmod +x run.sh trước)
python main.py    # hoặc: python -m kana_rush
```

Nếu là Python venv trên Ubuntu, dùng `python` từ venv (đã activate) hoặc
`pip install -e .` rồi gõ `kana-rush`.

### Tuỳ chọn CLI

| Tuỳ chọn | Ý nghĩa |
| --- | --- |
| `--no-delay` | bỏ hiệu ứng chờ giữa các màn hình (hữu ích khi demo/test) |
| `--no-color` | tắt màu (màn hình đơn sắc) |
| `--ascii` | thay ký tự Unicode bằng ASCII (hộp, thanh tiến trình) |
| `--seed N` | gieo hạt ngẫu nhiên (chọn câu hỏi xác định, dễ tái hiện) |
| `--save-path PATH` | thư mục chứa file lưu (mặc định `saves/`) |
| `--smoke-test` | chạy kịch bản tự động kiểm tra toàn trình (dùng save tạm) |
| `--help` | hướng dẫn sử dụng |

### Lưu tiến trình trên nhiều máy

File tiến trình nằm trong `saves/progress.json` (kèm bản backup
`progress.backup.json`) và được đồng bộ tự động qua git: mỗi lần mở game
fetch + so sánh `updated_at` (bản mới hơn thắng, không bao giờ merge conflict),
mỗi lần thoát (Lưu và thoát hoặc Ctrl+C) commit + push lên `origin/main`.

- Điều kiện: máy phải cài `git`, repo đã cấu hình remote `origin` và
  credential để push được.
- Đồng bộ là best-effort: lỗi/chậm chỉ hiện cảnh báo vàng, game vẫn chơi
  bình thường, tiến độ vẫn an toàn cục bộ.
- Tắt đồng bộ: `KANA_RUSH_NO_CLOUD=1` (Windows: `set KANA_RUSH_NO_CLOUD=1`).

## Kiểm thử

```powershell
python -m pytest -q
```

69 test bao gồm: dữ liệu, đầu vào, lịch trình SRS, điều kiện MASTERED, hàng đợi
học, chọn câu hỏi review, cặp nhầm lẫn, từ vựng, điểm số, thống kê, lưu/phục
hồi, các phiên học và nhiều kịch bản smoke cấp App + CLI.

## Cấu trúc

```
data/                 # hiragana.json, mnemonics_vi.json, confusion_pairs.json, words.json
kana_rush/            # mã nguồn (UI tách biệt hoàn toàn khỏi logic SRS)
tests/                # 15 file test
main.py               # điểm vào dòng lệnh
run.bat / run.sh      # launcher mở game nhanh
saves/                # tiến trình người chơi (đồng bộ qua git)
```

## Điểm cần biết

- Lần chạy đầu tiên sẽ hỏi: bắt đầu từ 0 hay làm bài **chẩn đoán** (đưa kana
  đã biết lên thẳng REVIEW).
- Thang gợi ý: gõ `?` (hoặc `hint`, `giup`) trong câu hỏi.
- Thoát giữa chừng: `quit` (hoặc `exit`, `thoat`); Ctrl+C vẫn lưu tiến trình.
