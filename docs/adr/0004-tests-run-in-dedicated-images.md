# 测试运行在专门构建的测试镜像，不借用生产容器

`web-test`（Node 工具链阶段）跑 Web 单元测试，`api-test`（API 镜像 + owner 环境变量）跑双数据库集成测试；两者都在 compose `test` profile 下经 `docker compose run --rm` 一次性执行。生产镜像与容器的凭据面、职责保持干净：owner 连接串只进入一次性测试容器，不进入 API 服务进程。
