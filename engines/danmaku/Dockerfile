# 本产品弹幕引擎（douyinLive v2.2.1 vendored 源码构建，替代外部 ghcr 镜像）
# 构建：docker build -t radar-engine-danmaku -f engines/danmaku/Dockerfile engines/danmaku
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY vendor/douyinLive/ /src/
RUN go build -o /out/douyinLive ./cmd/...

FROM alpine:3.20
COPY --from=build /out/douyinLive /usr/local/bin/douyinLive
EXPOSE 1088
CMD ["douyinLive", "--port", "1088"]
