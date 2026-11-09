# KEV-0.8B 下载与本地启动

## 下载来源及当前固定版本

| 项目 | 地址 | 版本 |
|---|---|---|
| 推理源码 | https://github.com/jaredpalmer/kev | `2074cc44256200bb37540103fa4572901faa10f3` |
| KEV 适配器与决策头 | https://huggingface.co/jaredpalmer/kev-0.8b/tree/v1.0 | `bf75a6a8848ea6960ff2ed108d9ed44c2941174f` |
| 底座 | https://huggingface.co/Qwen/Qwen3.5-0.8B-Base | `dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68` |

链接及版本在 2026-11-09 核对。KEV 不是只下载一个独立权重：首次启动会下载决策头/适配器及对应 Qwen 底座。两者均使用上游各自授权（模型卡标注 Apache-2.0），请阅读下载页。请勿执行 `pip install kev`：PyPI 同名包不是这个决策模型。

## 安装与启动

使用独立环境，不把重型推理依赖装进 Web 环境。需要 Python 3.12 或 3.13，推荐 3.12。

```bash
python3.12 -m venv .venv-kev
source .venv-kev/bin/activate
python -m pip install --upgrade pip
python -m pip install 'kev[serve] @ git+https://github.com/jaredpalmer/kev.git@2074cc44256200bb37540103fa4572901faa10f3'
python -m kev.serve --run jaredpalmer/kev-0.8b@bf75a6a8848ea6960ff2ed108d9ed44c2941174f --host 127.0.0.1 --port 8009
```

Windows 使用 `.venv-kev\Scripts\activate`。首次启动保持联网；模型保存在 Hugging Face 标准缓存中，不在 Git 仓库内。不要在首次下载前设置 `HF_HUB_OFFLINE=1`。如网络环境需要镜像或代理，由使用者按 Hugging Face 的方式自行配置。

macOS Apple Silicon 上游默认使用 MLX，也可以显式设置 `KEV_BACKEND=mlx`。其他平台按上游设备检测选择支持的后端；CPU 可以通过额外添加 `--device cpu` 运行，但不保证速度。上游已知 0.8B 支持 4GB 级 GPU；具体内存和速度随上下文长度、依赖和硬件变化。

安装完成后也可以直接使用本仓库脚本：

```bash
# 终端一：先安装 .venv-kev，再运行
bash scripts/start-kev.sh
# 终端二：先安装 .venv，再运行
bash scripts/start-web.sh
```

## 验证

```bash
curl http://127.0.0.1:8009/v1/models
```

应返回包含 `kev-0.8b` 的运行信息。本项目向 `/v1/systemone` 发送 `{model, state, questions}`，接收选项与概率；KEV 不替代云端中文理解与车机话术模型。端口目前固定 8009；Web 默认 10086。

可在同一台机器重新下载后使用 Hugging Face 缓存离线运行；如更换机器，需同时复制全部适配器和底座缓存，或重新下载。请勿将模型文件提交到 GitHub。
