# 当前本机配置

PostgreSQL 已切换到用户安装的 Windows 服务：

- 程序：`E:\PostgreSQL\17`
- 数据：`E:\PostgreSQL\17\data`
- 服务：`postgresql-x64-17`
- 地址：`127.0.0.1:5432`
- 数据库：`safeops_demo`、`safeops_test`
- 项目账号：`safeops`（非超级用户，随机密码保存在根目录 `.env`）

用户的 `postgres` 管理账号密码未修改，未写入项目。旧临时库的演示记录已删除，新演示库仅初始化合成数据。临时实例 `safeops-postgres-20260919` 及 `.safeops-tools` 下载缓存均已删除。

本机端口 8000 被 SalesPilot 使用，SafeOps 改用 8001，控制台仍为 http://127.0.0.1:3000。前端代理配置位于 `apps/web/.env.local`。这些本机配置文件不提交 Git；Compose 的默认容器配置不变。

```powershell
Set-Location 'langgraph-production'
conda activate '.conda-safeops'
# DATABASE_URL 从 .env 自动读取
safeops serve --port 8001

# 另开终端启动前端
Set-Location 'langgraph-production\apps\web'
npm run dev
```

切换后验证：新 PostgreSQL 上迁移成功，77 项测试全部通过（23.31 秒）；`http://127.0.0.1:8001/ready` 返回 ready，前端代理可读取新演示库。测试使用独立 `safeops_test` 数据库。
