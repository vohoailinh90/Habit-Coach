# Habit Coach

Repo theo dõi thói quen của bạn, vận hành bằng cách **nhắn tin**:

1. Bạn kể cho Claude mục tiêu và một ngày bình thường → Claude dựng **lịch tuần** và đưa vào **Google Calendar** (có chuông nhắc).
2. Mỗi ngày bạn nhắn việc **thật sự** đã làm: *"lên tàu"*, *"bắt đầu làm việc"*, *"đọc xong 1 trang"*. Claude ghi lại kèm giờ.
3. Một script (không phải cảm tính) so sánh **kế hoạch với thực tế** và cho ra phần trăm bám sát kế hoạch, chuỗi ngày liên tiếp, tỉ lệ đúng giờ.
4. **Mỗi Chủ nhật** Claude rà soát tuần, rồi chỉnh lịch tuần sau cho dễ giữ hơn: giảm thời lượng chỗ hay trượt, dời giờ về đúng thói quen thật, chỉ tăng độ khó khi bạn đã làm tốt ba tuần liền.
5. Xuyên suốt có lời nhắc và động viên: chuông Google Calendar, câu nhắc đúng lúc ("đừng bỏ hai lần liền"), chuỗi ngày, phần thưởng khi đạt mục tiêu tuần.

## Bắt đầu

Mở một phiên Claude Code trong repo này và nói: *"Mình muốn lập kế hoạch tuần."* Claude sẽ hỏi vài câu ngắn (vì sao bạn muốn làm, một ngày bình thường của bạn, múi giờ) rồi đề xuất kế hoạch nhỏ để bạn duyệt.

Hằng ngày chỉ cần nhắn tự nhiên:

| Bạn nhắn | Claude ghi |
|---|---|
| "Mình vừa lên tàu" | mốc `board-train` lúc giờ hiện tại |
| "Hồi 7:50 mình đã ngồi đọc sách" | `morning-read` lúc 07:50 |
| "Hôm nay chỉ đọc được một trang" | bản siêu nhỏ, vẫn giữ chuỗi (tính 60%) |
| "Nay ốm nên nghỉ" | bỏ qua có lý do, không tính là lơ là |
| "Quên nhắn, hôm qua 19:40 mình đi bộ" | bổ sung vào đúng ngày hôm qua |

## Lệnh hay dùng

```bash
pip install -r requirements.txt
python3 habit.py today                       # kế hoạch, tiến độ hôm nay và một lời nhắc
python3 habit.py log --habit morning-read "ngồi xuống đọc"     # ghi (Claude làm việc này khi bạn nhắn)
python3 habit.py report                      # báo cáo tuần này
python3 habit.py review --date 2026-10-04    # rà soát Chủ nhật (thêm --write --apply để lưu và áp dụng)
python3 habit.py ics                         # xuất calendar/habit-coach.ics
python3 habit.py nudge                       # lời nhắc đúng lúc này
python3 habit.py --lang ja today             # đổi ngôn ngữ: vi, ja, en
```

## Google Calendar

- **Cách tốt nhất:** kết nối Google Calendar cho Claude (claude.ai → Settings → Connectors → Google Calendar). Claude sẽ tạo thẳng các khối giờ, kèm chuông nhắc, vào một lịch riêng tên *Habit Coach*, và cập nhật mỗi Chủ nhật.
- **Chưa kết nối:** chạy `python3 habit.py ics`, rồi vào Google Calendar → Cài đặt → Nhập & xuất → Nhập `calendar/habit-coach.ics` vào lịch riêng *Habit Coach*. Mỗi sự kiện có mã cố định nên nhập lại sẽ cập nhật chứ không nhân đôi; thói quen đã bỏ khỏi kế hoạch thì cần xóa tay.
- Nên dùng **lịch riêng** để không đụng vào lịch thật của bạn.

## Lời nhắc tự động (tùy chọn)

Chuông Google Calendar (mặc định 10 phút trước mỗi khối) luôn hoạt động, kể cả khi không có phiên Claude nào. Nếu muốn thêm Claude chủ động nhắn cho bạn, tạo *routine* (lịch chạy định kỳ) cho repo này, ví dụ:

| Khi nào | Nội dung |
|---|---|
| Chủ nhật 20:15 (giờ của bạn) | "Chạy kỹ năng `weekly-review` cho tuần vừa rồi, áp dụng thay đổi, xuất lịch, commit và gửi tóm tắt 5 dòng." |
| Ngày thường 07:20 | "Chạy `python3 habit.py nudge --moment morning` và gửi lời nhắc kèm kế hoạch hôm nay." |
| Mỗi ngày 20:30 | "Chạy `python3 habit.py nudge --moment evening` rồi nhắc việc còn dở, ưu tiên bản siêu nhỏ." |

Mỗi lần chạy là một phiên Claude, nên đừng đặt quá hai lời nhắc mỗi ngày.

## Cấu trúc

```
habit.py            lệnh chạy (launcher)
habit_coach/        mã nguồn: chấm điểm, review, xuất lịch, lời nhắc, bản dịch vi/ja/en
config/profile.yaml múi giờ, ngôn ngữ, bạn là ai / vì sao, phần thưởng
plan/               kế hoạch hiện tại và các phiên bản cũ (mỗi tuần được chấm theo đúng phiên bản của nó)
logs/               nhật ký thật, mỗi ngày một file, chỉ ghi thêm
reviews/            báo cáo Chủ nhật
calendar/           file .ics đã xuất
tests/  scripts/    kiểm thử, kiểm tra dịch/bố cục/đột biến
CLAUDE.md           sổ tay vận hành cho Claude
```

## Quyền riêng tư

Nhật ký chứa giờ giấc đi lại hằng ngày của bạn. Giữ repo ở chế độ **private**.

## Cách chấm điểm

Mỗi mục trong kế hoạch được xếp đúng một trạng thái: **đúng giờ** (từ 90 phút trước đến 15 phút sau giờ dự kiến) = 100%, **trễ** (đến 2 giờ sau) = 70%, **làm ngoài khung giờ** trong ngày = 40%, **chỉ bản siêu nhỏ** nhân 60%, **bỏ lỡ** = 0%. Ngày nghỉ có lý do và mục chưa đến hạn không bị tính. Phần trăm tuần là trung bình có trọng số (1 đến 3) của các thói quen được chấm điểm; các mốc như "lên tàu" chỉ được theo dõi để review thấy ngày thật của bạn. Ngày kết thúc lúc 04:00 sáng nên đi ngủ 00:30 vẫn tính cho buổi tối hôm trước. Mọi ngưỡng nằm trong `habit_coach/scoring.py` và `habit_coach/review.py`.

---

**English, in short.** Habit Coach is a chat-driven habit tracker: Claude builds a weekly plan and exports it to Google Calendar (via the connector, or an `.ics` file); you message what you really did and when; `habit.py` scores plan vs. reality deterministically; every Sunday the `weekly-review` skill revises the plan to make it easier to keep, and nudges (calendar alarms, "never miss twice", streaks, rewards) keep you going. Catalogs: `vi`, `ja`, `en`. Developers: `pip install -r requirements-dev.txt`, then `pytest`, `python3 scripts/mutation_check.py --all`, `python3 scripts/i18n_check.py --require ja,en,vi`, `python3 scripts/layout_check.py`.
