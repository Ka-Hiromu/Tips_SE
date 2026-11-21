"""ONJO(.lst)取り込みの自動テスト（GUI・音声デバイス不要）。

一時フォルダに CP932 の .lst を作り、Preset への変換結果を検証する。
"""
import tempfile
from pathlib import Path

from onjo import ONJO_FADE_SEC, load_lst

PLAYER = """[Player{idx:02d}]
Key={key}
FileCount={count}
{files}Volume={vol}
isFadeIn={fin}
isFadeOut={fout}
isSolo={solo}
isRepeat={rep}
Start={start}
End={end}
Color=FFFFFFFF
"""


def player(idx, *, key=0, files=(), vol=0.8, fin=0, fout=0, solo=0, rep=0,
           start=-1, end=-1):
    body = "".join(f"FileName{i}={f}\n" for i, f in enumerate(files))
    return PLAYER.format(idx=idx, key=key, count=len(files), files=body,
                         vol=vol, fin=fin, fout=fout, solo=solo, rep=rep,
                         start=start, end=end)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # 実在する音源（空ファイルでよい。ここではパス解決のみを見る）
        for name in ("雨.mp3", "風.wav", "wind_a.mp3", "wind_b.mp3", "声.m4a"):
            (d / name).write_bytes(b"")

        text = "".join([
            # 0: 基本項目 + キー"1"(Key=2) + 存在しないフォルダ → 同フォルダから復帰
            player(0, key=2, files=[r"D:\no_such_dir\雨.mp3"], vol=0.5,
                   fin=1, fout=1, solo=1, rep=1),
            # 1: キー"A"(Key=11) + 再生範囲(2.0秒〜40.0秒)
            player(1, key=11, files=[str(d / "風.wav")],
                   start=20000000, end=400000000),
            # 2: 複数音源
            player(2, files=[str(d / "wind_a.mp3"), str(d / "wind_b.mp3")]),
            # 3: 1文字にできないキー（スペース）
            player(3, key=46, files=[str(d / "雨.mp3")]),
            # 4: 未対応フォーマット
            player(4, files=[str(d / "声.m4a")]),
            # 5: 未割当（取り込まれない）
            player(5),
            # 20: 飛び番号（グリッドが足りるか） + 逆向きの再生範囲
            player(20, key=53, files=[str(d / "風.wav")],
                   start=30000000, end=10000000),
        ])
        lst = d / "テスト.lst"
        lst.write_bytes(text.encode("cp932"))

        preset, rep = load_lst(lst)

        assert rep.pads == 6, rep.pads
        assert preset.rows * preset.cols >= 21, (preset.rows, preset.cols)
        assert preset.rows <= 8 and preset.cols <= 8

        p0 = preset.pads[0]
        assert p0.path == str(d / "雨.mp3"), p0.path          # 同フォルダから復帰
        assert rep.relocated == 1, rep.relocated
        assert abs(p0.gain - 0.5) < 1e-6
        assert p0.loop is True and p0.exclusive is True
        assert p0.fade_in == ONJO_FADE_SEC and p0.fade_out == ONJO_FADE_SEC
        assert p0.key == "1", p0.key
        print("基本項目・パス復帰・キー(数字) OK")

        p1 = preset.pads[1]
        assert p1.key == "a", p1.key
        assert abs(p1.start - 2.0) < 1e-6 and abs(p1.end - 40.0) < 1e-6, (p1.start, p1.end)
        print("キー(英字)・再生範囲 OK")

        p2 = preset.pads[2]
        assert p2.random_enabled and len(p2.sources) == 2
        assert abs(p2.sources[0].prob - 50.0) < 1e-6
        assert p2.random_active and p2.assigned
        print("複数音源 → 確率(均等)ランダム OK")

        assert preset.pads[3].key == "", preset.pads[3].key
        assert any("スペース" in w for w in rep.warnings), rep.warnings
        assert any(".m4a" in w for w in rep.warnings), rep.warnings
        assert not preset.pads[5].assigned
        assert preset.pads[20].key == "", preset.pads[20].key   # Key=53 は「→」
        assert preset.pads[20].end == 0.0                      # 逆向きなので無視
        assert any("再生範囲が逆" in w for w in rep.warnings), rep.warnings
        print("非対応キー・非対応フォーマット・逆範囲の警告 OK")

        # multi="first" では先頭だけ
        preset2, rep2 = load_lst(lst, multi="first")
        p2b = preset2.pads[2]
        assert not p2b.random_enabled and p2b.path == str(d / "wind_a.mp3")
        assert any("先頭だけ" in w for w in rep2.warnings), rep2.warnings
        print("multi=first OK")

    print("ALL ONJO TESTS PASSED")


if __name__ == "__main__":
    main()
