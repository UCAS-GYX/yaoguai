# 常驻 OCR 客户端：只启动一次 ./ocr --serve，之后每次识别只发一行图片路径（省掉进程启动 + 模型加载）。
# 线程安全（主线程和冻结监测线程都会识别）。常驻进程出错/超时 => 杀掉，下次自动重启；连续失败就退回单次模式。
import os, select, subprocess, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "ocr")
END = "<<END>>"


class OcrClient:
    def __init__(self, exe=EXE):
        self.exe = exe
        self.lock = threading.Lock()
        self.p = None
        self.fails = 0
        self.fail_t = 0.0

    def _start(self):
        self.p = subprocess.Popen([self.exe, "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def _kill(self):
        if self.p:
            try:
                self.p.kill()
                self.p.wait(timeout=2)
            except Exception:
                pass
            for f in (self.p.stdin, self.p.stdout):
                try:
                    f.close()
                except Exception:
                    pass
        self.p = None

    def _serve(self, path, timeout):
        if self.p is None or self.p.poll() is not None:
            self._start()
        self.p.stdin.write(path + "\n")
        self.p.stdin.flush()
        lines, t0, fd = [], time.time(), self.p.stdout
        while True:
            left = timeout - (time.time() - t0)
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise TimeoutError("ocr 常驻进程超时")
            line = fd.readline()
            if not line:
                raise RuntimeError("ocr 常驻进程退出")
            line = line.rstrip("\n")
            if line == END:
                return "\n".join(lines)
            lines.append(line)

    def run(self, path, timeout=8):
        """返回和 `ocr <图片>` 一样的文本（每行 text\\tx\\ty\\tw\\th）"""
        path = os.path.abspath(path)
        with self.lock:
            if self.fails < 3 or time.time() - self.fail_t > 60:   # 连续失败 3 次后改用单次模式，60 秒后再试常驻
                try:
                    out = self._serve(path, timeout)
                    self.fails = 0
                    return out
                except Exception:
                    self._kill()
                    self.fails += 1
                    self.fail_t = time.time()
            r = subprocess.run([self.exe, path], capture_output=True, text=True, timeout=timeout)
            return r.stdout

    def close(self):
        with self.lock:
            self._kill()


_DEFAULT = [None]


def ocr_text(path, timeout=8):
    if _DEFAULT[0] is None:
        _DEFAULT[0] = OcrClient()
    return _DEFAULT[0].run(path, timeout)
