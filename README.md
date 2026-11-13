# 网络数据采集管理平台

面向网页数据接入、定时增量采集、抽取规则管理、代理调度、限速策略、失败重试与采集结果验证的网络数据采集工程管理平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 采集源登记 | `datasource` | 采集源 | 源端编号、目标域名、入口URL |
| 解析规则 | `parserule` | 解析规则 | 规则编号、页面选择器、内容选择器 |
| 代理调度 | `proxyconfig` | 代理节点 | 节点编号、代理地址、代理端口 |
| 限速策略 | `ratelimiter` | 限速策略 | 策略编号、目标域名、每秒请求数 |
| 采集任务 | `taskcrawl` | 采集任务 | 任务编号、采集源名称、规则编号 |
| 预处理管道 | `pipeline` | 处理管道 | 管道编号、数据来源、字段映射 |
| RPA流程编排 | `robotflow` | 自动化流程 | 流程编号、入口页面、页面定位器 |
| 失败重试 | `retrypolicy` | 重试策略 | 策略编号、错误类型、最大重试次数 |
| 结果校验 | `resultcheck` | 校验规则 | 校验编号、目标字段、非空检查 |
| 采集告警 | `alertrule` | 告警规则 | 告警编号、监控指标、比较运算符 |
| 诊断记录 | `diagnose` | 诊断报告 | 报告编号、关联任务、异常类型 |
| 调度计划 | `scheduler` | 调度计划 | 计划编号、关联任务、cron表达式 |
| 输出配置 | `exportconf` | 输出配置 | 配置编号、输出格式、目标路径 |
| 登录态管理 | `lmcfg` | 登录凭证 | 凭证编号、目标站点、认证方式 |
| 反爬对抗 | `blacklist` | 对抗策略 | 策略编号、目标域名、UA模拟列表 |
| 采集日志 | `storagelog` | 日志记录 | 日志编号、关联任务、开始时间 |
| 模板库 | `templatelib` | 采集模板 | 模板编号、站点分类、模板名称 |
| 数据标注 | `datalabel` | 标注任务 | 标注编号、数据集名称、标注类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 模板版本与校验缓存

模板验证、任务排期和回滚动作共用同一套版本判定，实现在
`backend/app/fingerprint.py`（指纹）与 `backend/app/services/versioning.py`（缓存与失效传播）。

- **指纹口径**：模板指纹覆盖模板编号、站点分类、请求模板、分页配置、适用域名、字符编码、
  字段映射与解析规则集；规则集指纹覆盖每条规则的选择器、清洗正则与启用状态。
  请求模板、分页配置或字段映射任一变化都会换指纹，旧结论立即失效。
- **缓存复用**：同一模板 + 同一组解析规则在有效期内（`VALIDATION_TTL_SECONDS`，默认 300 秒）
  直接复用校验结论；重复提交同一指纹只保留一份结果。
- **原子字段组**：模板编号、适用域名、请求模板、字符编码必须同进同退，部分提交会被拦下。
- **并发修改**：验证过程中模板被改写时按最终配置重新判定，中间结论不落库。
- **任务判断**：任务只认当前版本指纹；模板从验证中变为已发布或已废弃时按当前版本重新判断
  能否启动。历史运行配置固化在任务上，只读展示，不随模板版本改写。
- **回滚**：回滚到旧生效版本时整版读取该版本的分页配置、字段映射与规则集，不与新版本拼接。
- **跨页联动**：模板详情的受影响任务（`/api/templatelib/{id}/impact`）与任务排期视图
  （`/api/taskcrawl/schedule`）、调度排期视图（`/api/scheduler/schedule`）使用同一指纹与同一
  受影响原因文案。

回归用例在 `backend/tests/`，运行方式：

```bash
cd backend
pip install -r requirements-dev.txt
PYTHONPATH=. pytest tests -q
```

领域：数据工程
子方向：数据采集、爬虫与RPA
