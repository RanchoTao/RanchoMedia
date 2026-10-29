"""Render local channel assets for the current Git article; never upload anything.

Run with .runtime/ecosystem/media/.venv/Scripts/python.exe. Dependencies are
Pillow==12.0.0 and imageio-ffmpeg==0.6.0. Only the successful new writer/reviewer
receipt is read; historical manuscripts and editorial approval are untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BG, WHITE, MUTED, ACCENT = "#101C2B", "#F5F4EF", "#B6C4D4", "#79E2BB"
FONT = Path("C:/Windows/Fonts/msyh.ttc")
BOLD = Path("C:/Windows/Fonts/msyhbd.ttc")
SCENES = [
    (
        "01 / 问题",
        "Git 提交之后\n网站就更新了吗？",
        "提交、推送、部署、页面核对\n各自需要一份证据。",
        "技术解读 · 流程示意",
        7,
    ),
    (
        "02 / 文件",
        "先确认\n改了哪些内容",
        "工作目录：检查目标段落\n与本次无关的改动，也要辨认。",
        "检查建议 · 非部署记录",
        7,
    ),
    (
        "03 / 提交",
        "默认提交记录\n暂存区的内容",
        "编辑器里的最新文字\n不一定已经进入本次提交。",
        "依据 S1 · Git commit 官方文档",
        8,
    ),
    (
        "04 / 推送",
        "推送更新\n远端引用与对象",
        "核对目标提交与远端状态。\n站点流程仍需另行检查。",
        "依据 S2 · Git push 官方文档",
        8,
    ),
    (
        "05 / 假设案例",
        "把「每月整理」\n改成「每周整理」",
        "这是一个假设性 Markdown 修改，\n不对应真实网站或部署结果。",
        "假设案例 · 禁止当作运行回执",
        8,
    ),
    (
        "06 / 对应关系",
        "目标文字\n进入哪个提交？",
        "再确认远端对应的版本，\n以及站点处理的是不是该版本。",
        "检查建议 · 非真实验证结果",
        8,
    ),
    (
        "07 / 页面",
        "打开目标页面\n核对具体段落",
        "流程启动、构建成功、部署成功，\n都应按各自证据记录。",
        "检查建议 · 页面结果需独立核对",
        8,
    ),
    (
        "08 / 来源",
        "进度写清楚\n证据逐段保留",
        "S1：git-scm.com/docs/git-commit\nS2：git-scm.com/docs/git-push",
        "原创文字卡 · 静音无旁白 · 待人工审核",
        8,
    ),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(BOLD if bold else FONT), size)


def lines(
    draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, width: int
) -> list[str]:
    result: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for char in paragraph:
            if current and draw.textlength(current + char, font=face) > width:
                result.append(current)
                current = ""
            current += char
        result.append(current)
    return result


def block(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    size: int,
    width: int,
    fill: str = WHITE,
    bold: bool = False,
) -> int:
    face = font(size, bold)
    x, y = xy
    for line in lines(draw, text, face, width):
        draw.text((x, y), line, font=face, fill=fill)
        y += int(size * 1.65)
    return y


def scene(path: Path, entry: tuple[str, str, str, str, int], index: int) -> None:
    im = Image.new("RGB", (1080, 1920), BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((72, 130, 1008, 166), radius=18, fill="#22354A")
    d.rounded_rectangle(
        (72, 130, 72 + 936 * (index + 1) // len(SCENES), 166), radius=18, fill=ACCENT
    )
    block(d, "RANCHO MEDIA  /  技术解读", (76, 220), 32, 920, MUTED)
    block(d, entry[0], (76, 382), 44, 920, ACCENT, True)
    end = block(d, entry[1], (72, 520), 78, 932, WHITE, True)
    block(d, entry[2], (76, max(950, end + 100)), 43, 920, MUTED)
    d.line((76, 1480, 1004, 1480), fill="#35485F", width=2)
    block(d, entry[3], (76, 1530), 31, 920, MUTED)
    block(
        d,
        f"{index + 1:02d} / {len(SCENES):02d}    本地候选素材 · 未上传",
        (76, 1680),
        30,
        920,
        ACCENT,
    )
    im.save(path)


def cover(path: Path, size: tuple[int, int], platform: str, title: str, subtitle: str) -> None:
    w, h = size
    im = Image.new("RGB", size, BG)
    d = ImageDraw.Draw(im)
    margin = int(w * 0.065)
    narrow = h < w * 0.6
    heading_size = int(w * (0.040 if narrow else 0.052))
    main_size = int(w * (0.066 if narrow else 0.085))
    block(
        d, platform + "  /  技术解读", (margin, int(h * 0.1)), heading_size, w - 2 * margin, ACCENT
    )
    block(
        d,
        title,
        (margin, int(h * (0.29 if narrow else 0.28))),
        main_size,
        w - 2 * margin,
        WHITE,
        True,
    )
    block(
        d,
        subtitle,
        (margin, int(h * 0.76)),
        int(w * (0.028 if narrow else 0.032)),
        w - 2 * margin,
        MUTED,
    )
    im.save(path)


def clock(value: int) -> str:
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d},000"


def source_receipt(network: Path) -> tuple[Path, dict, dict]:
    for name in ("review-call.json", "writer-call.json"):
        path = network / name
        if not path.exists():
            continue
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))
        if receipt.get("status") != "SUCCEEDED":
            continue
        output = Path(receipt["output"]).resolve()
        if not output.is_relative_to(network.resolve()):
            raise ValueError("Article output must stay in the current network run directory")
        data = json.loads(output.read_text(encoding="utf-8-sig"))
        if "article" not in data or not isinstance(data["article"].get("body"), str):
            continue
        if receipt.get("output_sha256") and sha(output) != receipt["output_sha256"]:
            raise ValueError("Successful receipt output hash mismatch")
        body = data["article"]["body"]
        if not all(term in body for term in ("提交", "推送", "假设", "[S1]", "[S2]")):
            raise ValueError("This renderer only supports the new source-grounded Git article")
        return output, receipt, data
    raise ValueError("No successful current reviewer/writer output found")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", type=Path, default=ROOT / ".runtime/network")
    args = parser.parse_args()
    network = args.network.resolve()
    article_path, receipt, data = source_receipt(network)
    assets = network / "assets"
    assets.mkdir(exist_ok=True)
    frames = assets / "frames"
    frames.mkdir(exist_ok=True)
    for index, entry in enumerate(SCENES):
        scene(frames / f"{index + 1:02d}.png", entry, index)
    covers = [
        (
            "douyin-cover.png",
            (1080, 1920),
            "抖音",
            "Git 提交之后\n网站就更新了吗？",
            "提交 → 推送 → 站点流程 → 页面核对",
        ),
        (
            "bilibili-cover.png",
            (1920, 1080),
            "BILIBILI",
            "Git 提交、推送、上线\n分别确认什么？",
            "流程示意 · 含明确标注的假设案例",
        ),
        (
            "xiaohongshu-cover.png",
            (1242, 1660),
            "小红书",
            "文件改好了\n网站更新了吗？",
            "一张检查清单：文件、提交、远端、页面",
        ),
        (
            "wechat-cover.png",
            (900, 383),
            "微信公众号",
            "从 Git 提交到网站可见",
            "技术解读 · 逐段核对版本与页面",
        ),
    ]
    for filename, size, platform, title, subtitle in covers:
        cover(assets / filename, size, platform, title, subtitle)
    concat = assets / "timeline.ffconcat"
    concat.write_text(
        "ffconcat version 1.0\n"
        + "".join(
            f"file 'frames/{index + 1:02d}.png'\nduration {entry[4]}\n"
            for index, entry in enumerate(SCENES)
        )
        + f"file 'frames/{len(SCENES):02d}.png'\n",
        encoding="utf-8",
    )
    start, subtitles = 0, []
    for index, (_, title, text, note, duration) in enumerate(SCENES):
        subtitles.append(
            f"{index + 1}\n{clock(start)} --> {clock(start + duration)}\n{title}\n{text}\n{note}\n"
        )
        start += duration
    (assets / "git-workflow.zh-CN.srt").write_text("\n".join(subtitles), encoding="utf-8")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    video = assets / "git-workflow-vertical.mp4"
    encode = [
        ffmpeg,
        "-y",
        "-v",
        "warning",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat),
        "-r",
        "30",
        "-t",
        str(start),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(video),
    ]
    encoded = subprocess.run(encode, capture_output=True, text=True, timeout=180, check=True)
    (assets / "ffmpeg-encode.log").write_text(encoded.stderr, encoding="utf-8")
    decode = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(video), "-map", "0:v:0", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    (assets / "ffmpeg-decode.log").write_text(
        decode.stderr or "Full video decode completed, exit 0; no errors.\n", encoding="utf-8"
    )
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video)], capture_output=True, text=True, timeout=20
    )
    (assets / "ffmpeg-media-info.log").write_text(probe.stderr, encoding="utf-8")
    duration_match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    if not duration_match or "1080x1920" not in probe.stderr or "Audio:" in probe.stderr:
        raise RuntimeError("Unexpected rendered media metadata")
    actual_duration = sum(
        float(value) * scale
        for value, scale in zip(duration_match.groups(), (3600, 60, 1), strict=True)
    )
    brief = json.loads((network / "git-publication.brief.json").read_text(encoding="utf-8-sig"))
    (assets / "sources.json").write_text(
        json.dumps(brief["sources"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "status": "LOCAL_ASSETS_RENDERED_NOT_UPLOADED",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "path": str(article_path),
            "sha256": sha(article_path),
            "run_key": receipt["run_key"],
            "title": data["article"]["title_options"][0],
        },
        "content_status": "AUTHOR_APPROVAL_REQUIRED",
        "adaptation": (
            "Original editorial text cards based only on the current Git article and S1/S2; "
            "no real deployment case claimed."
        ),
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("Pillow", "imageio-ffmpeg")
        },
        "font": {
            "path": str(FONT),
            "sha256": sha(FONT),
            "distribution": "Windows system font; not included in assets",
        },
        "ffmpeg_version": subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0],
        "video": {
            "file": video.name,
            "sha256": sha(video),
            "bytes": video.stat().st_size,
            "duration_seconds": actual_duration,
            "resolution": [1080, 1920],
            "fps": 30,
            "codec": "H.264",
            "pixel_format": "yuv420p",
            "audio": "SILENT_NO_NARRATION_NO_AUDIO_STREAM",
            "full_decode": "PASS_EXIT_0",
            "subtitle": "git-workflow.zh-CN.srt",
            "upload": "NOT_UPLOADED",
        },
        "images": [
            {"file": name, "resolution": list(size), "sha256": sha(assets / name)}
            for name, size, *_ in covers
        ],
        "platforms": {
            "douyin": {
                "title": "Git提交之后，网站就更新了吗？",
                "video": video.name,
                "cover": "douyin-cover.png",
                "status": "NOT_UPLOADED",
            },
            "bilibili": {
                "title": "Git提交、推送与上线：分别需要哪些确认",
                "video": video.name,
                "cover": "bilibili-cover.png",
                "status": "NOT_UPLOADED",
            },
            "xiaohongshu": {
                "title": "文件改好了，网站更新了吗？四个核对点",
                "cover": "xiaohongshu-cover.png",
                "note": "Cover only; native post copy still requires review",
                "status": "NOT_UPLOADED",
            },
            "wechat": {
                "title": data["article"]["title_options"][0],
                "cover": "wechat-cover.png",
                "note": "Cover only; no official draft created",
                "status": "NOT_UPLOADED",
            },
        },
        "acceptance_pending": [
            "Author content review",
            "Mobile screen playback and platform cover crop review",
            "Any upload or public publication requires separate scoped authorization",
        ],
    }
    (assets / "asset-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (assets / "README.md").write_text(
        "# 本轮 Git 技术解读素材\n\n状态：NOT_UPLOADED / AUTHOR_APPROVAL_REQUIRED。\n\n"
        "- git-workflow-vertical.mp4：1080×1920、H.264、30fps，静音无旁白，中文字幕文字卡。\n"
        "- git-workflow.zh-CN.srt：同步字幕。frames/：原创文字卡。\n"
        "- douyin-cover.png / bilibili-cover.png：各平台视频封面。\n"
        "- xiaohongshu-cover.png / wechat-cover.png：图文入口封面。\n"
        "- sources.json：S1、S2 官方文档来源，延用本轮研究检索时间。\n"
        "- asset-manifest.json：来源 SHA、平台标题、尺寸、时长、编码和验证证据。\n\n"
        "视频仅为新稿的技术解读改编。Markdown 修改案例是假设，没有宣称实际部署成功。"
        "封面未验收平台裁切，未上传，未创建远端草稿，未发布。"
        "微软雅黑来自 Windows，未分发字体文件。\n\n"
        "复跑：`.runtime/ecosystem/media/.venv/Scripts/python.exe "
        "scripts/render_channel_assets.py`。"
        "隔离依赖：Pillow==12.0.0、imageio-ffmpeg==0.6.0。\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest": str(assets / "asset-manifest.json"),
                "duration_seconds": actual_duration,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
