# 初始化由一次性 init 容器执行，服务进程不持高权限凭据

compose 中 `init` 服务以 `platform_owner` / `analytics_owner` 身份完成 Alembic 迁移与幂等 seed 后退出；`api` 服务以 `service_completed_successfully` 依赖 init，其环境里自始至终只有 `platform_app` 与 `analytics_readonly` 两条低权限连接串。「运行中的服务进程不持有高权限身份」因此在容器层面成立，而不依赖同一容器内的启动顺序，且每次启动都收敛到确定状态。

## 被否方案

- API 容器启动时以 owner 身份自行迁移：高权限凭据会常驻服务进程环境。

## 后果

- owner 凭据仍是本地开发默认值（数据库不发布宿主端口，风险局限在 compose 网络内）；生产化需要密钥管理。
