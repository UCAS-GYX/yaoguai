import Foundation
import Vision
import AppKit
// usage: ocr <image>  -> lines "text\tx\ty\tw\th" (normalized, origin top-left)
let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(1) }
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
