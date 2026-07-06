# CI/CD 简介

## 一句话概括

**CI/CD** = Continuous Integration（持续集成）/ Continuous Deployment（持续部署）。

把"每次改完代码 → 跑测试 → 部署上线"这套流程自动化。

---

## CI — 持续集成

每次提交代码到仓库，自动执行：

```
git push
  ↓
自动触发
  ↓
安装依赖 → 跑测试 → 跑 Lint → 类型检查
  ↓
全部通过 ✅    挂了 ❌ → 通知你修
```

**核心理念**：别等上线才发现 bug，每次改完代码立刻让机器帮你查。

最常用的免费方案是 **GitHub Actions**，在项目根目录放一个 `.github/workflows/ci.yml`：

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r backend/requirements.txt
      - run: pip install pytest
      - run: cd backend && python -m pytest tests/ -v
```

GitHub 免费提供虚拟机来跑这个，不需要自己的服务器。

---

## CD — 持续部署

测试全通过之后，自动把代码部署到目标环境：

```
git push → CI 全绿 → 自动打包 → 自动部署 → 用户用上新版本
```

部署目标可以是：
- 自己的 Linux 服务器（SSH + rsync）
- 云服务（阿里云、腾讯云、AWS 等）
- 容器平台（Docker + K8s）

---

## 对本项目的价值

当前状态：每次改代码后，需要手动敲 `pytest` 检查是否通过。

加上 CI 后：

| 场景 | 无 CI | 有 CI |
|------|-------|-------|
| 改完代码检查 | 手动跑测试 | push 后自动跑 |
| 半夜改代码 | 可能忘记跑测试 | 自动跑，挂了发邮件 |
| 多人协作 | 不知道别人改了啥 | PR 页面直接看到测试结果 |

---

## 进阶：CI 流水线可以跑什么

除了 `pytest`，还可以挂上：

- **Linter**（ruff/flake8）—— 检查代码风格
- **Type checker**（mypy/pyright）—— 检查类型正确性
- **Security scan**（bandit/safety）—— 检查依赖漏洞
- **Coverage**（pytest-cov）—— 检查测试覆盖率是否下降

本项目 70 个 Agent 测试，加了 CI 后每次 push 自动跑一次，工程化程度直接上一个台阶。
