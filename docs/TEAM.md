<!-- team-onboarding:file v1 -->
# Team agent cho repo này

Repo `vohoailinh90/Habit-Coach` đã được đăng ký với team agent dùng model miễn phí. Chi tiết cách cài và vận hành nằm ở repo
`vohoailinh90/claude-agent-routing-template`: `docs/vps-team.md` (team Claude) và `docs/vps-team-codex.md` (team Codex).
Hai team dùng chung worker miễn phí (tester, coder, reviewer); khác nhau ở người thiết kế và audit.

| Mục | Giá trị |
|---|---|
| Nhánh gốc | `main` |
| Mức nhạy cảm | `private` |
| Stack phát hiện | python |
| Lệnh cài cho gates (có mạng) | `python3 -m venv --system-site-packages .venv && .venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt` |
| Lệnh kiểm gợi ý | `.venv/bin/python -m pytest -q` |

Các giá trị này nằm trong `repos.conf` trên VPS, không nằm trong repo này (worker ghi được vào repo, nên cấu hình chạy lệnh
có mạng không được đặt ở đây). Muốn đổi thì sửa ở VPS.

## Chạy một feature

```bash
team new F12 --repo vohoailinh90/Habit-Coach      # team-codex nếu để Codex thiết kế và audit
nano /srv/team/work/F12/request.md                 # viết yêu cầu, tiếng Việt cũng được
# lead viết spec.md/tasks.md/acceptance.md  ->  team run F12 --detach  ->  lead audit  ->  team push F12
```

Mẫu `acceptance.md` (mỗi dòng phải thoát 0; trước khi code, tester phải làm chúng FAIL):

```bash
.venv/bin/python -m pytest -q
```

## Quy trình PR: Claude tự tạo PR, tự merge khi Codex và CI đều xanh

1. Khi công việc trong repo này sẵn sàng ship, Claude tự mở PR, không chờ ai yêu cầu, trừ khi người dùng chỉ yêu cầu một thay đổi cục bộ hoặc quy tắc riêng của repo nói PR chỉ được mở khi có yêu cầu.
2. Ngay khi PR mở, yêu cầu Codex review bằng đúng một bình luận trên PR: `@codex review` (thêm `@codex security review` khi quy tắc của repo hoặc chính thay đổi đòi hỏi: xác thực, phân quyền, bí mật, thanh toán, dữ liệu cá nhân). Codex không tự review. Mỗi head commit chỉ yêu cầu một lần cho mỗi lệnh review, không yêu cầu khi chưa có phản hồi, và không nhắc Codex trong mô tả PR hay commit message. Nếu repo có `scripts/codex_round_count.py` thì chạy nó trước mỗi lần yêu cầu.
3. Kiểm từng finding với code. Đúng: sửa, kèm test hồi quy khi lỗi có hành vi kiểm thử được (sửa chỉ là văn bản thì dùng các check thông thường của repo), push, rồi yêu cầu review lại trên head mới. Sai: trả lời trên thread kèm bằng chứng. Không giới hạn số vòng; P2/P3 đã bác bỏ có bằng chứng thì không chặn; P0/P1 không dàn xếp được thì báo chủ repo và không merge.
4. Tự merge, không chờ ai duyệt, khi cả bốn điều kiện cùng đúng trên head hiện tại: mọi review mà thay đổi này đòi hỏi (review thường, thêm security review khi đụng xác thực, phân quyền, bí mật, thanh toán, dữ liệu cá nhân) đã được yêu cầu cho đúng commit đó và không còn finding (một 👍 trơn không phải review, trừ khi quy tắc riêng của repo chấp nhận nó cho đúng lời gọi đó), CI xanh, không có conflict, và branch protection (hoặc merge queue) của nhánh gốc yêu cầu "up to date" và áp dụng cả với admin, và danh tính đang merge không có đường bỏ qua nó (danh sách bypass của ruleset, role tùy chỉnh, quyền của app). Ngay trước khi merge, đọc lại head của PR và của nhánh gốc: PR đổi head thì review lại; nhánh gốc đổi thì merge nó vào và kiểm lại. Merge gắn với head SHA đã kiểm. Thiếu protection đó thì không merge: để PR mở, ghi rõ nó sạch và vì sao chưa merge, báo chủ repo.
5. Codex không phản hồi sau 10 phút: thử lại đúng một lần cho mỗi head; thông báo lỗi hoặc hết hạn mức thì không cần chờ trước lần thử lại đó. Nếu Codex đã nhận nhưng không trả review, hoặc task của nó lỗi, không thử lại: PR là BLOCKED. PR cũng BLOCKED khi lần thử lại không có phản hồi. PR BLOCKED để mở và báo một lần; không merge khi chưa được review và không dùng đường khác (API, CLI, Action) để lấy review.

Mở PR và các bước trên là việc của Claude; `team push` chỉ đẩy nhánh `team/ID` (hoặc `team-codex/ID`).

Nếu chính repo này đã ghi quy tắc riêng cho một bước ở trên (ai merge, điều kiện merge, cách yêu cầu review, giới hạn số vòng), quy tắc đó quyết định bước ấy; phần repo không nói thì theo đây.

## Ghi chú riêng của repo này

- Exception in this repository (replaces the matching rule above): Data commits (logs/, plan/, reviews/, calendar/) are pushed to main without a pull request (CLAUDE.md "Persisting"); the pull-request process above applies to changes to the tool itself.
