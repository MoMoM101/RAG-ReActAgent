# 参与贡献

感谢你关注 RAG Agent。提交代码、文档或问题前，请先阅读本指南和 [安全策略](SECURITY.md)。

## 开始之前

- Python 3.12；
- Node.js 20 或更高版本；
- npm；
- 仅在运行 Compose 或 Docker E2E 时需要 Docker；
- 完整问答测试需要可用的 LLM 与 Embedding 服务。

请不要在 Issue、Pull Request、日志或提交中包含 API Key、密码、Token、真实业务文档、数据库、备份或个人信息。

## 安装开发环境

在项目根目录创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt -r backend\requirements-dev.txt
```

Linux 或 macOS：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
```

安装前端依赖：

```powershell
cd frontend
npm install
cd ..
```

创建本地配置：

```powershell
Copy-Item backend\.env.example backend\.env
```

至少设置新的 `JWT_SECRET` 和 `BOOTSTRAP_ADMIN_PASSWORD`。需要真实问答时，再配置 LLM 与 Embedding 服务。不要提交 `backend/.env`。

## 启动项目

统一启动：

```powershell
python main.py
```

前后端分别启动：

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
cd frontend
npm.cmd run dev
```

## 必须执行的质量检查

### 后端

在项目根目录执行静态检查：

```powershell
.\.venv\Scripts\python.exe -m ruff check backend --config pyproject.toml
cd backend
..\.venv\Scripts\python.exe -m mypy . --config-file ..\pyproject.toml
```

不使用真实模型服务的离线测试：

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests -m "not docker and not needs_llm and not needs_embedding"
```

与 CI 一致的非 Docker 测试：

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests -m "not docker"
```

如果本机配置了真实模型密钥，`needs_llm` 和 `needs_embedding` 会产生外部请求。只想离线验证时必须使用前一条命令。

### 前端

```powershell
cd frontend
npm.cmd run lint
npm.cmd test
npm.cmd run build
```

### Docker 验收

Docker 服务运行后，在项目根目录执行：

```powershell
.\scripts\docker_e2e_acceptance.ps1 -Clean
```

`-Clean` 只应用于测试环境，它会清理本次验收创建的容器和卷。不要对包含重要数据的 Compose 项目执行清理。

## 代码与设计要求

- Python 使用 Ruff 和 MyPy；
- TypeScript 使用严格类型检查和 Oxlint；
- 新行为必须配套自动化测试；
- 配置项变化必须同步 `backend/.env.example` 和 README；
- 数据库结构变化必须增加 Alembic 迁移；
- API 变化需要说明兼容性影响；
- 不通过扩大跳过范围、降低门禁或删除断言掩盖失败；
- 评测结果不得用于反向生成同一评测的标准答案；
- 外部输入、文档内容和工具结果均按不可信数据处理。

## 提交约定

建议使用 Conventional Commits：

- `feat:` 新功能；
- `fix:` 缺陷修复；
- `refactor:` 不改变外部行为的重构；
- `test:` 测试调整；
- `docs:` 文档调整；
- `ci:` 持续集成调整；
- `chore:` 依赖或维护工作。

一次提交应聚焦一个可解释目的，并说明修改原因、验证方法以及部署或数据影响。

## Pull Request 要求

提交前确认：

- [ ] 后端 Ruff 和 MyPy 通过；
- [ ] 后端相关测试通过；
- [ ] 前端代码检查、测试和生产构建通过；
- [ ] 新功能或缺陷修复包含测试；
- [ ] 没有提交 `.env`、密钥、密码、Token 或本地数据；
- [ ] 配置变化已同步环境变量模板；
- [ ] 数据库变化包含迁移与回退说明；
- [ ] 用户可见行为变化已同步文档；
- [ ] 已说明兼容性、安全、性能和数据影响；
- [ ] 提交中不存在无关格式化或生成文件。

Pull Request 描述应包含：问题背景、实现方案、验证结果、风险、回退方法和必要截图。截图必须隐藏个人信息与凭据。

## 报告问题

普通缺陷可以使用 GitHub Issue 模板。安全漏洞、认证绕过、密钥泄露或可导致数据访问的问题必须按照 [SECURITY.md](SECURITY.md) 私下报告，不要创建公开 Issue。

## 参考资料

- [项目参考](docs/PROJECT_REFERENCE.md)
- [环境变量模板](backend/.env.example)
- [更新日志](CHANGELOG.md)
- [行为准则](CODE_OF_CONDUCT.md)

