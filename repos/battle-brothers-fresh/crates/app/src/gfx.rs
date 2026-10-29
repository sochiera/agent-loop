//! Tiny software rasteriser: the whole UI is drawn into a CPU framebuffer which the
//! presenter uploads to the window each frame. This keeps rendering testable headlessly.

use crate::font;

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub struct Color {
    pub r: u8,
    pub g: u8,
    pub b: u8,
    pub a: u8,
}

pub const fn rgb(r: u8, g: u8, b: u8) -> Color {
    Color { r, g, b, a: 255 }
}

pub const fn rgba(r: u8, g: u8, b: u8, a: u8) -> Color {
    Color { r, g, b, a }
}

impl Color {
    /// Scale alpha by `f` in `0..=1`.
    pub fn fade(self, f: f32) -> Color {
        Color { a: (self.a as f32 * f.clamp(0.0, 1.0)) as u8, ..self }
    }

    pub fn lerp(self, o: Color, t: f32) -> Color {
        let m = |a: u8, b: u8| (a as f32 + (b as f32 - a as f32) * t.clamp(0.0, 1.0)) as u8;
        Color { r: m(self.r, o.r), g: m(self.g, o.g), b: m(self.b, o.b), a: m(self.a, o.a) }
    }

    pub fn darken(self, f: f32) -> Color {
        self.lerp(rgb(0, 0, 0), f)
    }

    pub fn lighten(self, f: f32) -> Color {
        self.lerp(rgb(255, 255, 255), f)
    }
}

#[derive(Clone)]
pub struct Framebuffer {
    pub w: usize,
    pub h: usize,
    /// Pixels packed as little-endian RGBA (byte order R, G, B, A in memory).
    pub px: Vec<u32>,
}

impl Framebuffer {
    pub fn new(w: usize, h: usize) -> Self {
        Framebuffer { w, h, px: vec![0xFF00_0000; w * h] }
    }

    pub fn bytes(&self) -> &[u8] {
        bytemuck::cast_slice(&self.px)
    }

    /// Copy a rectangle of `src` (same size) onto `self` unchanged; used for cached backdrops.
    pub fn blit_region(&mut self, src: &Framebuffer, x: usize, y: usize, w: usize, h: usize) {
        debug_assert_eq!((self.w, self.h), (src.w, src.h));
        let x1 = (x + w).min(self.w);
        for yy in y..(y + h).min(self.h) {
            let row = yy * self.w;
            self.px[row + x..row + x1].copy_from_slice(&src.px[row + x..row + x1]);
        }
    }

    pub fn clear(&mut self, c: Color) {
        let v = pack(c);
        self.px.fill(v);
    }

    pub fn pixel(&self, x: usize, y: usize) -> Color {
        let v = self.px[y * self.w + x];
        rgba(v as u8, (v >> 8) as u8, (v >> 16) as u8, (v >> 24) as u8)
    }

    #[inline]
    fn blend(&mut self, x: i32, y: i32, c: Color, coverage: f32) {
        if x < 0 || y < 0 || x as usize >= self.w || y as usize >= self.h {
            return;
        }
        let a = (c.a as f32 / 255.0) * coverage;
        if a <= 0.0 {
            return;
        }
        let i = y as usize * self.w + x as usize;
        if a >= 0.999 {
            self.px[i] = pack(c);
            return;
        }
        let d = self.px[i];
        let mix = |s: u8, d: u32| (s as f32 * a + (d & 0xFF) as f32 * (1.0 - a)) as u32;
        let r = mix(c.r, d);
        let g = mix(c.g, d >> 8);
        let b = mix(c.b, d >> 16);
        self.px[i] = r | (g << 8) | (b << 16) | 0xFF00_0000;
    }

    pub fn fill_rect(&mut self, x: i32, y: i32, w: i32, h: i32, c: Color) {
        let x0 = x.max(0);
        let y0 = y.max(0);
        let x1 = (x + w).min(self.w as i32);
        let y1 = (y + h).min(self.h as i32);
        if c.a == 255 {
            let v = pack(c);
            for yy in y0..y1 {
                let row = yy as usize * self.w;
                self.px[row + x0 as usize..row + x1.max(x0) as usize].fill(v);
            }
        } else {
            for yy in y0..y1 {
                for xx in x0..x1 {
                    self.blend(xx, yy, c, 1.0);
                }
            }
        }
    }

    pub fn rect_outline(&mut self, x: i32, y: i32, w: i32, h: i32, t: i32, c: Color) {
        self.fill_rect(x, y, w, t, c);
        self.fill_rect(x, y + h - t, w, t, c);
        self.fill_rect(x, y + t, t, h - 2 * t, c);
        self.fill_rect(x + w - t, y + t, t, h - 2 * t, c);
    }

    /// Fill a convex polygon with horizontal edge anti-aliasing.
    pub fn fill_poly(&mut self, pts: &[(f32, f32)], c: Color) {
        let min_y = pts.iter().map(|p| p.1).fold(f32::MAX, f32::min).floor().max(0.0) as i32;
        let max_y = pts.iter().map(|p| p.1).fold(f32::MIN, f32::max).ceil().min(self.h as f32) as i32;
        for y in min_y..max_y {
            let yc = y as f32 + 0.5;
            let (mut xmin, mut xmax) = (f32::MAX, f32::MIN);
            for i in 0..pts.len() {
                let (x0, y0) = pts[i];
                let (x1, y1) = pts[(i + 1) % pts.len()];
                if (y0 <= yc && y1 > yc) || (y1 <= yc && y0 > yc) {
                    let x = x0 + (yc - y0) / (y1 - y0) * (x1 - x0);
                    xmin = xmin.min(x);
                    xmax = xmax.max(x);
                }
            }
            if xmin > xmax {
                continue;
            }
            let ix0 = xmin.floor() as i32;
            let ix1 = xmax.ceil() as i32;
            for x in ix0..ix1 {
                let cov = ((x as f32 + 1.0).min(xmax) - (x as f32).max(xmin)).clamp(0.0, 1.0);
                self.blend(x, y, c, cov);
            }
        }
    }

    pub fn poly_outline(&mut self, pts: &[(f32, f32)], t: f32, c: Color) {
        for i in 0..pts.len() {
            let a = pts[i];
            let b = pts[(i + 1) % pts.len()];
            self.line(a.0, a.1, b.0, b.1, t, c);
        }
    }

    pub fn line(&mut self, x0: f32, y0: f32, x1: f32, y1: f32, t: f32, c: Color) {
        let (dx, dy) = (x1 - x0, y1 - y0);
        let len = (dx * dx + dy * dy).sqrt().max(0.0001);
        let (nx, ny) = (-dy / len * t / 2.0, dx / len * t / 2.0);
        self.fill_poly(&[(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], c);
    }

    /// Anti-aliased disc (`r_in <= 0`) or ring between `r_in` and `r_out`.
    pub fn circle(&mut self, cx: f32, cy: f32, r_in: f32, r_out: f32, c: Color) {
        let x0 = (cx - r_out - 1.0).floor() as i32;
        let x1 = (cx + r_out + 1.0).ceil() as i32;
        let y0 = (cy - r_out - 1.0).floor() as i32;
        let y1 = (cy + r_out + 1.0).ceil() as i32;
        for y in y0..y1 {
            for x in x0..x1 {
                let d = ((x as f32 + 0.5 - cx).powi(2) + (y as f32 + 0.5 - cy).powi(2)).sqrt();
                let outer = (r_out - d + 0.5).clamp(0.0, 1.0);
                let inner = if r_in > 0.0 { (d - r_in + 0.5).clamp(0.0, 1.0) } else { 1.0 };
                self.blend(x, y, c, outer * inner);
            }
        }
    }

    pub fn disc(&mut self, cx: f32, cy: f32, r: f32, c: Color) {
        self.circle(cx, cy, 0.0, r, c);
    }

    pub fn text(&mut self, x: i32, y: i32, s: &str, scale: i32, c: Color) {
        let mut cx = x;
        for ch in s.chars() {
            let g = font::glyph(ch);
            for (row, bits) in g.iter().enumerate() {
                for col in 0..5 {
                    if bits & (0x10 >> col) != 0 {
                        self.fill_rect(cx + col * scale, y + row as i32 * scale, scale, scale, c);
                    }
                }
            }
            cx += font::advance(scale);
        }
    }

    /// Text with a one-pixel-step dark drop shadow for legibility over the map.
    pub fn text_shadow(&mut self, x: i32, y: i32, s: &str, scale: i32, c: Color) {
        self.text(x + scale, y + scale, s, scale, rgba(0, 0, 0, 200));
        self.text(x, y, s, scale, c);
    }

    pub fn text_centered(&mut self, cx: i32, y: i32, s: &str, scale: i32, c: Color) {
        let w = font::text_width(s, scale);
        self.text(cx - w / 2, y, s, scale, c);
    }
}

fn pack(c: Color) -> u32 {
    c.r as u32 | (c.g as u32) << 8 | (c.b as u32) << 16 | 0xFF00_0000
}

/// Encode the framebuffer as an (uncompressed) PNG. Used by `--screenshot` and tests.
pub fn encode_png(fb: &Framebuffer) -> Vec<u8> {
    fn crc32(data: &[u8]) -> u32 {
        let mut crc = 0xFFFF_FFFFu32;
        for &b in data {
            crc ^= b as u32;
            for _ in 0..8 {
                crc = if crc & 1 != 0 { (crc >> 1) ^ 0xEDB8_8320 } else { crc >> 1 };
            }
        }
        !crc
    }
    fn chunk(out: &mut Vec<u8>, kind: &[u8; 4], data: &[u8]) {
        out.extend_from_slice(&(data.len() as u32).to_be_bytes());
        let mut body = kind.to_vec();
        body.extend_from_slice(data);
        out.extend_from_slice(&body);
        out.extend_from_slice(&crc32(&body).to_be_bytes());
    }
    // Raw scanlines: filter byte 0 + RGB.
    let mut raw = Vec::with_capacity(fb.h * (1 + fb.w * 3));
    for y in 0..fb.h {
        raw.push(0);
        for x in 0..fb.w {
            let c = fb.pixel(x, y);
            raw.extend_from_slice(&[c.r, c.g, c.b]);
        }
    }
    // zlib stream made of stored deflate blocks.
    let mut z = vec![0x78, 0x01];
    let mut chunks = raw.chunks(65535).peekable();
    while let Some(block) = chunks.next() {
        z.push(if chunks.peek().is_none() { 1 } else { 0 });
        z.extend_from_slice(&(block.len() as u16).to_le_bytes());
        z.extend_from_slice(&(!(block.len() as u16)).to_le_bytes());
        z.extend_from_slice(block);
    }
    let (mut a, mut b) = (1u32, 0u32);
    for &v in &raw {
        a = (a + v as u32) % 65521;
        b = (b + a) % 65521;
    }
    z.extend_from_slice(&((b << 16) | a).to_be_bytes());

    let mut out = vec![0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A];
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&(fb.w as u32).to_be_bytes());
    ihdr.extend_from_slice(&(fb.h as u32).to_be_bytes());
    ihdr.extend_from_slice(&[8, 2, 0, 0, 0]);
    chunk(&mut out, b"IHDR", &ihdr);
    chunk(&mut out, b"IDAT", &z);
    chunk(&mut out, b"IEND", &[]);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rect_and_clipping() {
        let mut fb = Framebuffer::new(10, 10);
        fb.fill_rect(-5, -5, 8, 8, rgb(255, 0, 0));
        assert_eq!(fb.pixel(0, 0), rgb(255, 0, 0));
        assert_eq!(fb.pixel(2, 2), rgb(255, 0, 0));
        assert_eq!(fb.pixel(3, 3), rgb(0, 0, 0));
        fb.fill_rect(8, 8, 50, 50, rgb(0, 255, 0));
        assert_eq!(fb.pixel(9, 9), rgb(0, 255, 0));
    }

    #[test]
    fn polygon_and_circle_cover_center() {
        let mut fb = Framebuffer::new(40, 40);
        fb.fill_poly(&[(5.0, 5.0), (35.0, 5.0), (20.0, 35.0)], rgb(10, 200, 30));
        assert_eq!(fb.pixel(20, 15), rgb(10, 200, 30));
        assert_eq!(fb.pixel(2, 2), rgb(0, 0, 0));
        fb.disc(20.0, 20.0, 5.0, rgb(1, 2, 3));
        assert_eq!(fb.pixel(20, 20), rgb(1, 2, 3));
    }

    #[test]
    fn text_draws_pixels() {
        let mut fb = Framebuffer::new(60, 20);
        fb.text(1, 1, "AB", 2, rgb(255, 255, 255));
        let lit = fb.px.iter().filter(|&&p| p & 0xFFFFFF != 0).count();
        assert!(lit > 20);
    }

    #[test]
    fn png_has_signature_and_ihdr() {
        let fb = Framebuffer::new(3, 2);
        let png = encode_png(&fb);
        assert_eq!(&png[..8], &[0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A]);
        assert_eq!(&png[12..16], b"IHDR");
        assert_eq!(u32::from_be_bytes(png[16..20].try_into().unwrap()), 3);
        assert_eq!(u32::from_be_bytes(png[20..24].try_into().unwrap()), 2);
    }
}
