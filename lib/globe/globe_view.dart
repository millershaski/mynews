import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'geo.dart';

/// Interactive globe: drag to rotate, pinch to zoom, tap a country to select it.
/// Its look follows [month]: snow cover, sea ice and vegetation change with the seasons.
class GlobeView extends StatefulWidget {
  final List<Country> countries;
  final int month;
  final String? selectedCode;
  final ValueChanged<Country> onSelect;

  const GlobeView({
    super.key,
    required this.countries,
    required this.month,
    required this.selectedCode,
    required this.onSelect,
  });

  @override
  State<GlobeView> createState() => _GlobeViewState();
}

class _GlobeViewState extends State<GlobeView> with SingleTickerProviderStateMixin {
  double _lon = -40, _lat = 25, _zoom = 1;
  double _zoomAtStart = 1;
  bool _userTouched = false;
  late final Ticker _ticker;
  Duration _lastTick = Duration.zero;

  // Fly-to animation state.
  double? _fromLon, _fromLat, _toLon, _toLat;
  Duration _flyStart = Duration.zero;
  static const _flyDuration = Duration(milliseconds: 700);

  @override
  void initState() {
    super.initState();
    _ticker = createTicker(_onTick)..start();
  }

  @override
  void didUpdateWidget(GlobeView old) {
    super.didUpdateWidget(old);
    final code = widget.selectedCode;
    if (code != null && code != old.selectedCode) {
      final c = widget.countries.where((c) => c.code == code).firstOrNull;
      if (c != null) _flyTo(c.labelLon, c.labelLat);
    }
  }

  void _flyTo(double lon, double lat) {
    _userTouched = true;
    _fromLon = _lon;
    _fromLat = _lat;
    _toLon = _lon + normalizeLon(lon - _lon); // take the short way round
    _toLat = lat.clamp(-70.0, 70.0);
    _flyStart = _lastTick;
    if (!_ticker.isActive) _ticker.start();
  }

  void _onTick(Duration elapsed) {
    final dt = (elapsed - _lastTick).inMicroseconds / 1e6;
    _lastTick = elapsed;
    if (_toLon != null) {
      final t = ((elapsed - _flyStart).inMicroseconds / _flyDuration.inMicroseconds).clamp(0.0, 1.0);
      final e = Curves.easeInOut.transform(t);
      setState(() {
        _lon = normalizeLon(_fromLon! + (_toLon! - _fromLon!) * e);
        _lat = _fromLat! + (_toLat! - _fromLat!) * e;
      });
      if (t >= 1) _toLon = null;
    } else if (!_userTouched) {
      setState(() => _lon = normalizeLon(_lon + 6 * dt)); // gentle spin until the first touch
    } else {
      _ticker.stop();
      _lastTick = Duration.zero;
    }
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  double _radius(Size s) => math.min(s.width, s.height) * 0.45 * _zoom;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, box) {
      final size = Size(box.maxWidth, box.maxHeight);
      return Semantics(
        label: 'Globe. ${seasonCaption(widget.month)}. Tap a country to explore its news.',
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onScaleStart: (_) {
            _userTouched = true;
            _toLon = null;
            _zoomAtStart = _zoom;
          },
          onScaleUpdate: (d) {
            final r = _radius(size);
            setState(() {
              _zoom = (_zoomAtStart * d.scale).clamp(0.8, 4.0);
              _lon = normalizeLon(_lon - d.focalPointDelta.dx / r * 180 / math.pi);
              _lat = (_lat + d.focalPointDelta.dy / r * 180 / math.pi).clamp(-75.0, 75.0);
            });
          },
          onTapUp: (d) {
            _userTouched = true;
            final proj = Ortho(_lon, _lat, size.center(Offset.zero), _radius(size));
            final ll = proj.invert(d.localPosition);
            if (ll == null) return;
            final c = countryAt(widget.countries, ll.$1, ll.$2);
            if (c != null) widget.onSelect(c);
          },
          child: CustomPaint(
            size: size,
            painter: GlobePainter(
              countries: widget.countries,
              month: widget.month,
              selectedCode: widget.selectedCode,
              lon: _lon,
              lat: _lat,
              zoom: _zoom,
            ),
          ),
        ),
      );
    });
  }
}

class GlobePainter extends CustomPainter {
  final List<Country> countries;
  final int month;
  final String? selectedCode;
  final double lon, lat, zoom;

  GlobePainter({
    required this.countries,
    required this.month,
    required this.selectedCode,
    required this.lon,
    required this.lat,
    required this.zoom,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final r = math.min(size.width, size.height) * 0.45 * zoom;
    final proj = Ortho(lon, lat, center, r);
    final sphere = Rect.fromCircle(center: center, radius: r);

    // Atmosphere glow.
    canvas.drawCircle(
      center,
      r * 1.08,
      Paint()
        ..shader = RadialGradient(
          colors: [const Color(0x6680C8FF), const Color(0x0080C8FF)],
          stops: const [0.88, 1.0],
        ).createShader(Rect.fromCircle(center: center, radius: r * 1.08)),
    );

    // Ocean.
    canvas.drawCircle(
      center,
      r,
      Paint()
        ..shader = const RadialGradient(
          center: Alignment(-0.35, -0.4),
          colors: [Color(0xFF2D6FB0), Color(0xFF123E6E)],
        ).createShader(sphere),
    );

    canvas.save();
    canvas.clipPath(Path()..addOval(sphere));

    // Graticule every 30 degrees.
    final grid = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.5
      ..color = const Color(0x22FFFFFF);
    for (var la = -60.0; la <= 60; la += 30) {
      _polyline(canvas, proj, [for (var lo = -180.0; lo <= 180; lo += 5) (lo, la)], grid);
    }
    for (var lo = -180.0; lo < 180; lo += 30) {
      _polyline(canvas, proj, [for (var la = -90.0; la <= 90; la += 5) (lo, la)], grid);
    }

    // Sea ice grows and shrinks with the month.
    final ice = Paint()..color = const Color(0xCCE8F1F8);
    canvas.drawPath(proj.band(seaIceNorth(month), 90), ice);
    canvas.drawPath(proj.band(-90, seaIceSouth(month)), ice);

    // Countries with seasonal vegetation colours.
    final land = Path();
    final border = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.6
      ..color = const Color(0x99263238);
    Path? selected;
    for (final c in countries) {
      final p = proj.ringsPath(c.rings);
      if (p == null) continue;
      canvas.drawPath(p, Paint()..color = landColor(c, month));
      canvas.drawPath(p, border);
      land.addPath(p, Offset.zero);
      if (c.code == selectedCode) selected = p;
    }

    // Snow cover on land: a soft edge band, then solid snow towards the poles.
    canvas.save();
    canvas.clipPath(land);
    final north = snowLineNorth(month), south = snowLineSouth(month);
    canvas.drawPath(proj.band(north - 4, north), Paint()..color = const Color(0x66FFFFFF));
    canvas.drawPath(proj.band(north, 90), Paint()..color = const Color(0xE6FFFFFF));
    canvas.drawPath(proj.band(south, south + 4), Paint()..color = const Color(0x66FFFFFF));
    canvas.drawPath(proj.band(-90, south), Paint()..color = const Color(0xE6FFFFFF));
    canvas.restore();

    if (selected != null) {
      canvas.drawPath(selected, Paint()..color = const Color(0x88FFC107));
      canvas.drawPath(
        selected,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2
          ..color = const Color(0xFFFFA000),
      );
    }

    // Lighting: darken towards the edge so the sphere reads as 3D.
    canvas.drawCircle(
      center,
      r,
      Paint()
        ..shader = const RadialGradient(
          center: Alignment(-0.35, -0.4),
          radius: 1.1,
          colors: [Color(0x00000000), Color(0x00000000), Color(0x77000000)],
          stops: [0, 0.6, 1],
        ).createShader(sphere),
    );
    canvas.restore();
  }

  void _polyline(Canvas canvas, Ortho proj, List<(double, double)> pts, Paint paint) {
    final path = Path();
    var pen = false;
    for (final (lo, la) in pts) {
      final (o, v) = proj.project(lo, la);
      if (!v) {
        pen = false;
        continue;
      }
      pen ? path.lineTo(o.dx, o.dy) : path.moveTo(o.dx, o.dy);
      pen = true;
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(GlobePainter old) =>
      old.lon != lon ||
      old.lat != lat ||
      old.zoom != zoom ||
      old.month != month ||
      old.selectedCode != selectedCode ||
      old.countries != countries;
}
