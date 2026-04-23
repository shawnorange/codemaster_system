import AppKit
import Foundation
import PDFKit
import Vision

struct PageResult: Codable {
    let page_number: Int
    let text: String
}

struct OCRResponse: Codable {
    let pdf_path: String
    let pages: [PageResult]
}

private func argumentValue(flag: String) -> String? {
    let arguments = CommandLine.arguments
    guard let index = arguments.firstIndex(of: flag), index + 1 < arguments.count else {
        return nil
    }
    return arguments[index + 1]
}

private func groupObservations(_ observations: [VNRecognizedTextObservation]) -> [String] {
    struct Item {
        let text: String
        let box: CGRect
    }

    let items = observations.compactMap { observation -> Item? in
        guard let candidate = observation.topCandidates(1).first else {
            return nil
        }
        return Item(text: candidate.string, box: observation.boundingBox)
    }.sorted { lhs, rhs in
        if abs(lhs.box.midY - rhs.box.midY) > 0.01 {
            return lhs.box.midY > rhs.box.midY
        }
        return lhs.box.minX < rhs.box.minX
    }

    var grouped: [[Item]] = []
    for item in items {
        if let last = grouped.last, let lineY = last.first?.box.midY, abs(lineY - item.box.midY) <= 0.012 {
            grouped[grouped.count - 1].append(item)
        } else {
            grouped.append([item])
        }
    }

    return grouped.map { lineItems in
        lineItems
            .sorted { $0.box.minX < $1.box.minX }
            .map(\.text)
            .joined(separator: " ")
    }
}

private func renderPage(_ page: PDFPage) -> CGImage? {
    let bounds = page.bounds(for: .mediaBox)
    let scale: CGFloat = 2.5
    let width = Int(bounds.width * scale)
    let height = Int(bounds.height * scale)
    let colorSpace = CGColorSpaceCreateDeviceRGB()
    let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue

    guard let context = CGContext(
        data: nil,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: colorSpace,
        bitmapInfo: bitmapInfo
    ) else {
        return nil
    }

    context.setFillColor(NSColor.white.cgColor)
    context.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
    context.saveGState()
    context.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: context)
    context.restoreGState()
    return context.makeImage()
}

private func runOCR(pdfPath: String) throws -> OCRResponse {
    guard let document = PDFDocument(url: URL(fileURLWithPath: pdfPath)) else {
        throw NSError(domain: "pdf_vision_ocr", code: 2, userInfo: [NSLocalizedDescriptionKey: "无法打开 PDF"])
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["zh-Hans", "en-US"]

    var pages: [PageResult] = []
    for pageIndex in 0..<document.pageCount {
        guard let page = document.page(at: pageIndex), let image = renderPage(page) else {
            continue
        }

        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])
        let observations = request.results ?? []
        let lines = groupObservations(observations)
        pages.append(PageResult(page_number: pageIndex + 1, text: lines.joined(separator: "\n")))
    }

    return OCRResponse(pdf_path: pdfPath, pages: pages)
}

guard let pdfPath = argumentValue(flag: "--pdf"), !pdfPath.isEmpty else {
    fputs("usage: swift pdf_vision_ocr.swift --pdf /abs/path/file.pdf\n", stderr)
    exit(1)
}

do {
    let response = try runOCR(pdfPath: pdfPath)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let data = try encoder.encode(response)
    if let output = String(data: data, encoding: .utf8) {
        print(output)
    } else {
        throw NSError(domain: "pdf_vision_ocr", code: 3, userInfo: [NSLocalizedDescriptionKey: "编码 OCR 输出失败"])
    }
} catch {
    fputs("OCR failed: \(error.localizedDescription)\n", stderr)
    exit(2)
}
