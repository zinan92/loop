# loop

## 要去哪里
双侧执行框架:Mini 端 claimer(领活-干活-自合并)+ MBP 端 merger 退役后的三机器闸;整条流水线机器自治,人只出现在红线。

## 现在在哪里(2026-07-22)
- claimer v1 上线:60s 轮询 Dev Queue、fail-closed 闸门、park-approved 标签校验(仅 zinan92 添加有效)。
- 已废:人审 approve、链式叠 PR、ready-is-delivery(自动挡取代)。
- 未达成:0722 新法适配(机器段可选化)、打回返工回路、红线 CI 门、launchd 托管——全部在 Dev Queue。

## 下一步
- loop#15 新法适配 → #13 返工回路 → #14 红线 CI 门 → launchd KeepAlive 落地。