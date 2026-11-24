# Northbank Fitness 并发、边界与死锁问题统一归类报告

> 项目：COMP3019J-Team-Six（Northbank Fitness Centre）
> 生成日期：2026-11-24
> 范围：`app/` 下 access / booking / scheduling / membership / models 模块
> 状态：A、B、C、D、E、F 六类已修复并通过全部测试；仅剩若干忽略点（MySQL 集成测试等）待处理。

> **合并说明（2026-11-24 追加）**：本报告最初在独立 fix 分支产出。合并回 main
> 时发现团队的 main 分支已经用「no-op UPDATE 行锁 + `lock_membership` +
> `_locked_booking` + `locked_active_membership`」做了一套更完善、对 SQLite 也
> 真正生效的锁实现，覆盖了本报告的 C 类（锁顺序）。因此最终合并采用：
> - **保留 main 的锁实现**（其 `lock_member_schedule` 的 no-op UPDATE 比本报告
>   早先用 `SELECT FOR UPDATE` 的正确，SQLite 下后者是空操作）。
> - **补入 main 缺失的四类**：A（时钟）、D（读路径 `cleanup_expired`）、
>   E（`IntegrityError` 捕获）、F（`active_card` 排序统一 + cancel 退信用 active
>   守卫）。
> - **B 类（队列锁）本次未并入**：main 的 cancel/confirm/decline 已各自先
>   `_lock_class` 再调 `_offer_next`，写路径的队列推进已被课程锁保护；仅
>   `expire_offers`/`close_finished` 这俩惰性清理仍无锁，风险低、留作后续。
> 最终提交在分支 `fix/merge-concurrency-onto-main`（基于 origin/main，无冲突）。

---

## 0. 一句话结论

系统用「成员行锁 + 课程行锁」串行化写操作，设计方向正确，但存在四类同源缺陷：
**时钟混用、锁顺序不一致、无锁读改写、约束缺失**。这些缺陷在默认 SQLite
（整库锁 + 30s busy timeout）下被掩藏，切到 MySQL/InnoDB 后会以
`OperationalError (1213, Deadlock found)` 或丢失更新/超卖的形式暴露。

---

## 1. 锁模型总览

| 锁 | 实现 | 目标 |
|---|---|---|
| 成员锁 `lock_member_schedule(uid)` | `SELECT ... FROM user WHERE id=:uid FOR UPDATE` | 单个 user 行 |
| 课程锁 `_lock_class(sid)` | `UPDATE class_session SET capacity=capacity WHERE id=:sid` | 单个 class_session 行 |

约定锁顺序：**成员锁 → 课程锁**。SQLite 下 `FOR UPDATE` 是空操作，真正的锁是
整库文件写锁；`connect_args={"timeout": 30}` 让写者排队而不是立即失败。

---

## 2. 问题统一归类（同源问题 → 同一解法）

### A 类：两套时钟混用（已修复 ✓）

**同源：** 时间戳字段存 UTC（`datetime.utcnow`），但部分写入和比较误用本地
`datetime.now`，导致跨字段比较错位。

**受影响位置（同一解法，统一时钟约定）：**

| 位置 | 原代码 | 后果 |
|---|---|---|
| `booking._offer_next` | `offered_at = now()` | offer 窗口错位 |
| `booking.seconds_left` | `now = now()` | 非 UTC 时区（本机 UTC+08）offer 提前 8h 过期 |
| `booking.expire_offers` | `now = now()` | 同上 |
| `booking.open_offers` | `now = now()` | 同上 |
| `booking.rejoin` | `booked_at = now()` | FIFO 排序破坏，新成员插到队头 |
| `scheduling.class_edit`（免审批） | `restarted = now()` | 重置 offered_at 用错时钟 |
| `scheduling.respond_to_proposal` | `restarted = now()` | 同上 |

**统一解法：** 时间戳字段一律 `datetime.utcnow()`；与用户时钟（`start`/`end`/
`date.today()`/cut-off）比较一律 `datetime.now()`。二者不混比。

已修复 7 处：booking.py(243, 419, 426, 452, 543)、scheduling.py(409, 589)。

---

### B 类：无锁读-改-写（丢失更新/超卖）—— 已修复 ✓

**同源：** 对 booking 队列「先读、改 status、commit」全程无锁。

**受影响位置（同一解法，收拢到课程锁内）：**
- `booking.expire_offers`（遍历 Offered → Passed/重发）
- `booking._offer_next`（held_count 判断后 Waiting → Offered）
- `booking.close_finished`（结束课程的 Offered/Waiting → Cancelled）

**统一解法：** 三者作用在同一 class_session 的队列上，统一成「带课程锁的队列
推进函数」，入口先 `_lock_class(session_id)` 再改队列行。

已修复：
- `_offer_next`：内部首行 `_lock_class(session_id)`，成为唯一队列推进串行点。
- `expire_offers`：改 booking 状态前对 `sorted(sessions)` 逐个 `_lock_class`，
  且 `offered_at is None` 补写也纳入锁范围。
- `close_finished`：改前对 `sorted({class_session_id})` 逐个 `_lock_class`。

嵌套锁说明：`confirm` 等已在事务里持课程锁的路径调用它们时，同事务内对同一行
重复 `_lock_class` 是幂等可重入的（SQLite/InnoDB 均不自我冲突），故安全。
`sorted` 升序锁定避免多 session 场景下的 AB-BA 死锁。

---

### C 类：锁顺序不一致（InnoDB 死锁）—— 已修复 ✓

**同源：** 既拿课程锁又改成员 booking 行，但部分路径漏拿成员锁，形成
「课程 → 成员」逆序，与 `book`/`confirm` 的「成员 → 课程」相反。

**省略详述补全（前一轮被压缩的结论）：**

逆序死锁如何形成：
1. `class_edit` 免审批分支原只是 `UPDATE class_session` 拿课程锁，随后
   `_keep_cancel_open`/`_drop_clashing_waiters` 直接改多个成员的
   `late_change_cancel`/`offered_at`/`status`，**没有先锁这些成员的 user 行**。
2. 同一时刻某成员在 `book()`（锁自己的 user 行 → 等课程锁）。
3. 于是 A 事务持课程锁、等成员行；B 事务持成员行锁、等课程锁 → AB-BA 死锁。
4. SQLite 整库锁掩盖此问题（只串行排队）；MySQL/InnoDB 才报 1213 死锁，而
   路由只 `except OperationalError` 提示「系统繁忙」，不重试、不埋日志。

**受影响位置：**
- `scheduling.class_edit` 免审批分支（漏成员锁）
- `booking.cancel`（只课程锁）
- `booking.decline`（只课程锁）
- `respond_to_proposal` 已有 `_lock_members_for_time_change`（正确，是基准）

**统一解法：** 强制唯一锁顺序「成员锁 → 课程锁」。凡动成员 booking 行的路径，
必须 `lock_member_schedule` 在前、`_lock_class` 在后。

已修复：
- `class_edit` 免审批分支：`_apply_class_form` 前插入
  `locked_member_ids = _lock_members_for_time_change(class_row)`，并复用该结果
  传给 `validate_class`。
- `cancel`：`_lock_class` 前补 `lock_member_schedule(current_user.id)`。
- `decline`：`_lock_class` 前补 `lock_member_schedule(current_user.id)`。

---

### D 类：读路径触发写事务 —— 已修复 ✓

**同源：** GET/读路径里调 `close_finished`/`expire_offers` + `commit`。

**受影响位置：**
- `access.my_bookings`、`booking.offer_page`、`booking.login_destination`
- `booking.open_offers` → `expire_offers`
- `booking.attendance_list`

**统一解法：** 提取单一入口 `cleanup_expired()`，合并 `close_finished` + `expire_offers`
的调用，且**只在确有到点数据时才落库**：`close_finished` 有改动才 commit，
`expire_offers` 自己仅在改动时 commit。纯读（GET my-bookings、登录跳转、offer 页）
不再无条件开写事务。`open_offers` 去掉内部的 `expire_offers` 调用，成为纯读函数。

已修复：
- 新增 `booking.cleanup_expired()`。
- `my_bookings`、`offer_page`、`login_destination`、`attendance_list` 改调
  `cleanup_expired()`，移除散落的 `commit()`。
- `open_offers` 移除内部 `expire_offers()`，改为纯读。

---

### E 类：约束缺失 / TOCTOU（先查后写）—— 已修复 ✓

**同源：** 靠「先查询再插入」保证唯一性，无 DB 唯一约束兜底或未捕获
`IntegrityError`。

**受影响位置：**
- `membership.register`：查 email 后插，并发同 email → IntegrityError 未捕获 → 500
- `scheduling.instructor_accounts`：同上
- active card 唯一性：完全无约束，靠应用层时序

**统一解法：** DB 唯一约束 + 所有创建路径 `except IntegrityError: rollback + flash`。

已修复：register、instructor_accounts 两处补 `IntegrityError` 捕获（active card
唯一约束仍未加，见忽略点）。

---

### F 类：边界 / 卡片口径 —— 已修复 ✓

**受影响位置（经逐项验证后归纳）：**
1. ✅ `active_card`（membership.py）与 `active_membership`（access.py）重复实现且
   排序字段不一致（前者 `start.desc`、后者 `end.desc`），同一成员多卡时两个调用
   方会取到不同的卡。
2. ✅ `booking.cancel` 退信用到已 `replaced`/非 active 的旧卡，信用复活到无效卡。
3. ⚠️ `membership._renewal_end` 的「当天到期当天续期少算一天」**经验证不成立**：
   `current_end == today` 时 `>=` 与 `>` 两个分支的 base 都是 today，结果同为
   `today + duration`，无 off-by-one。故不修改（保留原语义）。

**统一解法：** 卡片查询收敛为单一权威实现；退信用只退给仍 active 的卡。

已修复：
- `membership.active_card` 委托 `access.active_membership`（单一实现，`end.desc`）。
- `booking.cancel` 增加 `membership.status == "active"` 守卫，只对有效卡退信用。

---

## 3. 目前仍忽略的点

1. **无 MySQL/InnoDB 集成测试**：现有 `tests/` 全跑 SQLite，掩盖锁顺序与无锁
   bug。切 MySQL 后 B/C 类才暴露，且无死锁复现、无锁顺序测试。
2. **`except OperationalError` 粒度太粗**：无法区分「约束冲突（不重试）」与
   「死锁/锁等待（可重试）」。
3. **`active_membership`（access.py）与 `active_card`（membership.py）重复实现**，
   未来改一处漏一处，应合并为单一 helper。
4. **前端读路径无锁**：`book_block`/`places_left` 用 relationship Python 求和，
   与写路径`held_count`SQL 聚合存在 identity-map 读旧值 + 读写不一致；
   按钮是尽力提示、提交才权威，需明确此语义。
5. **`rejoin` 重置 `booked_at` 的 FIFO 语义无测试锁死**（A 类修复后需补断言）。
6. **offer 过期是惰性的**：无真正定时器，无人访问时 Offered 席位无限悬挂，占用
   `places_left`。需后端定时任务（与 D 类统一）。

---

## 4. 已修复清单（本次会话）

| 类别 | 文件 | 改动 |
|---|---|---|
| A | booking.py:243,419,426,452,543 | `datetime.now()` → `datetime.utcnow()` |
| A | scheduling.py:409,589 | `restarted = now()` → `utcnow()` |
| B | booking.py `_offer_next` | 首行 `_lock_class(session_id)` |
| B | booking.py `expire_offers` | 改前 `sorted(sessions)` 逐个 `_lock_class` |
| B | booking.py `close_finished` | 改前 `sorted(session_ids)` 逐个 `_lock_class` |
| E | membership.py | 导入 IntegrityError + register 捕获 |
| E | scheduling.py | 导入 IntegrityError + instructor_accounts 捕获 |
| C | scheduling.py | class_edit 免审批分支补 `_lock_members_for_time_change` |
| C | booking.py | cancel/decline 补 `lock_member_schedule` |
| D | booking.py | 新增 `cleanup_expired`；读路径改调它，移除散落 commit |
| D | booking.py | `open_offers` 改为纯读 |
| F | membership.py | `active_card` 委托 `active_membership`（单一实现） |
| F | booking.py | `cancel` 退信用加 `status == active` 守卫 |

**验证：** smoke 48/48、account_membership 23/23、instructor_workflow 17/17、
edit_clash 6/6、concurrency_test PASS、approval_booking_concurrency_test PASS。

---

## 5. 建议落地顺序（剩余）

1. 忽略点 1/2：补 MySQL 锁顺序 + 死锁集成测试，细化 OperationalError 分类
2. 忽略点 6：offer 惰性过期改后台定时任务（D 类已收敛读路径，但如果长期无人
   访问，Offered 席位仍会悬挂占用 `places_left`）
