# Northbank Fitness Centre — 迭代改动记录

> 本文档记录从原版出发的所有代码改动，便于向老师汇报、复盘与后续维护。
> 对应测试：`tests/smoke_test.py`（22 项）、`tests/edit_clash_test.py`（5 项）、
> `tests/concurrency_test.py`（新增，并发校验）。当前全部通过。

## 一、运行环境（非代码改动）

1. 在 `northbank/` 下创建独立虚拟环境 `.venv`，安装 `requirements.txt` 依赖。
2. 重建 seed 数据库：原版 `instance/northbank.db` 中的课程日期已过期，导致
   timetable 空表。删除旧库后重新 `flask --app run seed` 生成未来课程。

## 二、功能改动

### 1. 新增「Manage classes」管理页

原版 staff 无编辑入口（`class_edit`、`roster` 路由已存在但无任何链接指向）。

- `app/scheduling.py`：新增 `GET /admin/classes`（`class_list`），仅 staff 可访问，
  列出全部课程（含过去与未来），每行带 Edit / Roster。
- `templates/classes_manage.html`：新增模板。
- `templates/base.html`：导航栏「New class」改为「Manage classes」。
- `static/style.css`：新增 `.manage-head`、`.actions`、`.btn.ghost` 样式。

### 2. 职责分离（staff 与 member 界面解耦）

- `templates/timetable.html`：移除 staff 视角每行的 Edit/Roster 链接，timetable
  回归「浏览 + 会员订课」单一职责。
- `templates/base.html`：staff 导航只显示「Manage classes」「Plans」，不显示
  「My bookings」。
- `app/access.py`：`my_bookings` 路由对 staff 直接 `abort(403)`（staff 不订课）。

### 3. member 订课体验修正

原版 `class_detail` 对已订课会员仍显示「Book this class」按钮，点下去才被拒绝。

- `app/access.py`：`class_detail` 路由新增 `booked` 判断并传入模板。
- `templates/class_detail.html`：已订课显示「you are booked」；满员显示「full」；
  staff 显示 Edit/Roster。
- `templates/timetable.html`：member 已订 / 满员状态展示完整。

## 三、容量修改一致性（回应「会员不知情」顾虑）

**结论**：原版在数据层面已防止超订（`book()` 实时读最新容量；`validate_class`
拒绝容量低于已订人数）。缺的是 staff 侧的「知情」与提示。

- `app/scheduling.py`：
  - 容量低于已订人数时的错误信息改为更明确的
    「That capacity is below the N members already booked. Remove bookings first,
    or cancel the class.」。
  - `class_edit` 渲染时传入 `booked=booked_count(...)`。
- `templates/class_form.html`：capacity 字段 label 显示「· N booked」，并加即时
  JS 警告（低于已订 → 红色「save will be refused」；等于已订 → 黄色提示）。
- `static/style.css`：新增 `.warn` 警告样式。

## 四、并发安全加固（回应「同时操作会出错吗」）

**结论**：写锁保证「读容量 → 判断 → 写」原子性，不会超订或数据矛盾。原版的风险
在于 SQLite 锁冲突时会立即报 `database is locked`（因 debug 服务器串行处理请求，
开发环境几乎不触发，但真实并发下会 500）。

- `config.py`：SQLite 增加 `busy_timeout=30` 秒（`SQLALCHEMY_ENGINE_OPTIONS`
  → `connect_args`），锁冲突时排队而非立即失败。
- `app/booking.py`：`book()` / `cancel()` 捕获 `OperationalError`，友好提示重试。
- `app/scheduling.py`：`class_new()` / `class_edit()` 捕获 `OperationalError`。

## 五、布局修复

- `static/style.css`：`body` 加 `min-height:100vh` + flex 列布局，`main` 加
  `flex:1`，修复内容少时 footer 不贴底的问题。

## 六、测试

- `tests/smoke_test.py`：更新容量错误文案断言（「cannot fall below」→
  「below the … already booked」）。
- `tests/concurrency_test.py`：新增，15 线程同订同一门课，验证不超订、不报错。

## 验证状态

- `tests/smoke_test.py`：22/22 通过。
- `tests/edit_clash_test.py`：5/5 通过。
- `tests/concurrency_test.py`：PASS（无 500、无超订）。
- 所有页面改动均经真实 HTTP 请求实测（staff / 管理 / 编辑 / 详情 / 预订）。

## 未改动（原样保留）

- 数据模型（`models.py` 九个表结构）。
- `seed.py` 种子数据本身。
- 预订核心规则：容量锁定、去重、时间重叠检查、credits 扣减、两小时取消截止。
- `class_form.html` 的乐观并发检测（`base_*` hidden 字段检测他人已保存）。

## 附：测试账号（密码均为 `password`）

| Email | 角色 |
|---|---|
| alex@example.com | member，月卡 unlimited |
| mei@example.com | member，十次卡 |
| staff@example.com | staff |

---

## 七、账号与方案（需求 1-4）

> 分支 `account-and-membership`（已 merge `origin/main`）。对应测试
> `tests/account_membership_test.py`（23 项）。
>
> ⚠️ 需求 5（为什么不能订/取消、到期提醒）最终由队友的 `booking.book_block` /
> `cancel_block` / `plan_notice` 及课表筛选、候补 waitlist 实现，在合并时采用队友
> 版本；本分支早期写的 `book_refusal` / `can_cancel` 草案已删除。本节只记录
> 需求 1-4（账号与方案）。

### 1. 注册

- `app/membership.py`：新增 `GET/POST /register`，会员自行建立账号（姓名、邮箱、
  密码、确认密码，密码至少 6 位，邮箱唯一）。注册成功即自动登录，并提示到「我的
  方案」页开通方案。
- `templates/register.html`：新增注册表单；`base.html`、`login.html` 增加「Register」入口。

### 2. 修改密码

- `app/membership.py`：新增 `GET/POST /account/password`，仅已登录会员可改自己的
  密码（校验当前密码、新密码长度与二次确认）。
- `templates/change_password.html`：新增表单；`account.html` 有入口链接。

### 3. 续订当前方案（不新开第二张卡）

- `app/membership.py`：`POST /account/renew`。同一张 `Membership` 卡上直接更新：
  `end` 从当前结束日往后延 `plan.duration_days`；有课时上限的方案补满到
  `credit_limit`。不新建卡（测试断言 active 卡仍只有一张）。
- 已到期卡从今天起延（不重复付费已过去的天数），未到期卡从自己的结束日起延
  （`_renewal_end`）。

### 4. 改订另一个方案（旧卡→已更换，新卡从今天起算）

- `app/membership.py`：`POST /account/switch`。旧卡 `status="replaced"`，新建一张卡
  `start=today`、`end=today+duration_days`、课时按新方案 `credit_limit` 重给。
- 已订的课不动：`Booking.membership_id` 仍指向旧卡，课表和 My bookings 原样显示。
- `app/models.py`：新增 `PlanChange` 表记录每次 open/renew/switch 事件（结束日与
  课时前后值，switch 时 `linked_id` 关联新旧卡），在「我的方案」页展示历史。
- `app/models.py`：`Membership` 增加 `plan_name`、`is_expired`、`credits_exhausted`
  三个只读属性。

### 合并 origin/main（冲突解决）

- 与我改动重叠的「为什么不能订/取消」采用队友实现，5 个冲突文件
  （`access.py`、`booking.py`、`timetable.html`、`class_detail.html`、
  `my_bookings.html`）取队友版本。
- 删除我分支的死代码 `book_refusal` / `can_cancel` / `membership_covering`。
- 保留我独有的需求 1-4（`membership.py` 路由 + `PlanChange` + 三个新模板 + 测试）。
- 界面语言统一为英语（`a012277`）。

### 验证状态

- `tests/smoke_test.py`：22/22 通过。
- `tests/edit_clash_test.py`：5/5 通过。
- `tests/concurrency_test.py`：PASS。
- `tests/account_membership_test.py`：23/23 通过。
- 真实 HTTP 实测：注册→换方案→续订→账号页 全部 200，状态正确；无冲突标记残留。
