import Foundation
import Vision
import AppKit
// usage: ocr <image>      -> lines "text\tx\ty\tw\th" (normalized, origin top-left)
//        ocr --serve      -> 常驻模式：从标准输入每行读一个图片路径，输出识别结果，最后一行输出 <<END>>
//                            （省掉每次识别都启动一次进程、加载一次 Vision 模型的开销）

func recognize(_ path: String) {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.usesLanguageCorrection = false
    let h = VNImageRequestHandler(cgImage: cg, options: [:])
    try? h.perform([req])
    for o in (req.results ?? []) {
        guard let t = o.topCandidates(1).first else { continue }
        let b = o.boundingBox
        print(String(format: "%@\t%.3f\t%.3f\t%.3f\t%.3f", t.string, b.minX, 1 - b.maxY, b.width, b.height))
    }
}

let args = CommandLine.arguments
if args.count > 1 && args[1] == "--serve" {
    setvbuf(stdout, nil, _IOLBF, 0)                    // 行缓冲：每行立刻发给调用方
    while let line = readLine(strippingNewline: true) {
        let p = line.trimmingCharacters(in: .whitespaces)
        if p.isEmpty { continue }
        autoreleasepool { recognize(p) }
        print("<<END>>")
    }
} else if args.count > 1 {
    recognize(args[1])
} else {
    exit(1)
}
