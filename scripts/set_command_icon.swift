// 給 .command 檔貼自訂圖示（Finder / Dock 顯示用）。
//
// 為什麼這樣做：圖示存在檔案的 FinderInfo + ResourceFork 擴充屬性，腳本內容不變、
// git 看不到差異；不用裝任何工具，Command Line Tools 的 swift 就能跑。
//
// 用法（在專案根目錄）：
//   swift scripts/set_command_icon.swift render <符號名> <輸出.png> [#RRGGBB]
//       用 SF Symbols 渲染一張 512px 圖示：圓角 100、白色符號 300pt semibold、指定底色
//   swift scripts/set_command_icon.swift apply <圖.png> <目標檔> [<目標檔>...]
//       把 PNG 貼到檔案上（NSWorkspace.setIcon）
//   swift scripts/set_command_icon.swift clear <目標檔>
//       移除自訂圖示
//
// 2026-09-13：PDF-Organize.command 用 tray.and.arrow.down.fill、底色 #2F7D5B。
import AppKit

func die(_ msg: String) -> Never { FileHandle.standardError.write((msg + "\n").data(using: .utf8)!); exit(1) }

func color(fromHex hex: String) -> NSColor {
    var h = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
    guard h.count == 6, let v = UInt32(h, radix: 16) else { die("底色要用 #RRGGBB：\(hex)") }
    h = ""
    return NSColor(srgbRed: CGFloat((v >> 16) & 0xff) / 255, green: CGFloat((v >> 8) & 0xff) / 255,
                   blue: CGFloat(v & 0xff) / 255, alpha: 1)
}

func render(symbol: String, to path: String, background: NSColor) {
    let size: CGFloat = 512, radius: CGFloat = 100
    guard let base = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) else {
        die("找不到 SF Symbol：\(symbol)")
    }
    let cfg = NSImage.SymbolConfiguration(pointSize: 300, weight: .semibold)
        .applying(.init(paletteColors: [.white]))
    guard let sym = base.withSymbolConfiguration(cfg) else { die("符號設定失敗") }

    let img = NSImage(size: NSSize(width: size, height: size))
    img.lockFocus()
    NSGraphicsContext.current?.imageInterpolation = .high
    let rect = NSRect(x: 0, y: 0, width: size, height: size)
    background.setFill()
    NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
    // 符號置中；可用區域留 8% 邊
    let s = sym.size
    let scale = min((size * 0.68) / s.width, (size * 0.68) / s.height, 1.0)
    let w = s.width * scale, h = s.height * scale
    sym.draw(in: NSRect(x: (size - w) / 2, y: (size - h) / 2, width: w, height: h),
             from: .zero, operation: .sourceOver, fraction: 1)
    img.unlockFocus()

    guard let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else { die("PNG 輸出失敗") }
    do { try png.write(to: URL(fileURLWithPath: path)) } catch { die("寫檔失敗：\(error)") }
    print("✓ 渲染 \(symbol) → \(path)")
}

let args = Array(CommandLine.arguments.dropFirst())
guard let cmd = args.first else { die("用法見檔頭註解") }
switch cmd {
case "render":
    guard args.count >= 3 else { die("render <符號名> <輸出.png> [#RRGGBB]") }
    render(symbol: args[1], to: args[2], background: color(fromHex: args.count > 3 ? args[3] : "#2F7D5B"))
case "apply":
    guard args.count >= 3, let img = NSImage(contentsOfFile: args[1]) else { die("apply <圖.png> <目標檔>...") }
    for target in args.dropFirst(2) {
        guard FileManager.default.fileExists(atPath: target) else { die("找不到檔案：\(target)") }
        let ok = NSWorkspace.shared.setIcon(img, forFile: target, options: [])
        print(ok ? "✓ 已貼圖示 → \(target)" : "✗ 貼圖示失敗 → \(target)")
        if !ok { exit(1) }
    }
case "clear":
    guard args.count >= 2 else { die("clear <目標檔>") }
    let ok = NSWorkspace.shared.setIcon(nil, forFile: args[1], options: [])
    print(ok ? "✓ 已移除圖示 → \(args[1])" : "✗ 移除失敗")
default:
    die("未知指令：\(cmd)")
}
