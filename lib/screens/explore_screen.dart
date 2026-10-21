import 'package:flutter/material.dart';

import '../api.dart';
import '../globe/geo.dart';
import '../globe/globe_view.dart';
import '../models.dart';
import '../repository.dart';
import '../widgets/common.dart';
import 'latest_screen.dart';

/// Oldest month the globe archive search supports (matches the server's EARLIEST).
const int earliestYear = 1999;

/// Home screen: spin the globe, pick a month and a country, and get one story from there.
/// The pick for each country + month + category is chosen once by the server and shared by everyone.
class ExploreScreen extends StatefulWidget {
  final NewsRepository repo;
  const ExploreScreen({super.key, required this.repo});

  @override
  State<ExploreScreen> createState() => _ExploreScreenState();
}

class _ExploreScreenState extends State<ExploreScreen> {
  late final Future<List<Country>> _countries = Country.load();
  late int _year, _month;
  String _category = ExploreCategory.defaults.first.id;
  Country? _selected;

  Loaded<ExploreResult>? _result;
  String? _error;
  bool _loading = false;
  int _request = 0; // drops responses that arrive after the user changed the selection

  final _sheet = DraggableScrollableController();

  @override
  void initState() {
    super.initState();
    final now = DateTime.now().toUtc();
    _year = now.year;
    _month = now.month;
  }

  @override
  void dispose() {
    _sheet.dispose();
    super.dispose();
  }

  // UTC, because the server decides "current month" in UTC.
  (int, int) get _latestMonth {
    final now = DateTime.now().toUtc();
    return (now.year, now.month);
  }

  bool get _atLatest => (_year, _month) == _latestMonth;
  bool get _atEarliest => _year == earliestYear && _month == 1;

  void _shiftMonth(int delta) {
    var m = _month + delta, y = _year;
    while (m < 1) {
      m += 12;
      y--;
    }
    while (m > 12) {
      m -= 12;
      y++;
    }
    _setMonth(y, m);
  }

  void _setMonth(int year, int month) {
    final (ly, lm) = _latestMonth;
    if (year * 12 + month > ly * 12 + lm || year < earliestYear) return;
    setState(() {
      _year = year;
      _month = month;
    });
    if (_selected != null) _load();
  }

  void _setCategory(String id) {
    if (id == _category) return;
    setState(() => _category = id);
    if (_selected != null) _load();
  }

  void _select(Country c) {
    setState(() => _selected = c);
    _load();
    if (_sheet.isAttached && _sheet.size < 0.4) {
      _sheet.animateTo(0.45, duration: const Duration(milliseconds: 300), curve: Curves.easeOut);
    }
  }

  Future<void> _load() async {
    final c = _selected;
    if (c == null) return;
    final id = ++_request;
    setState(() {
      _loading = true;
      _error = null;
      _result = null;
    });
    try {
      final r = await widget.repo.explore(country: c.code, year: _year, month: _month, category: _category);
      if (mounted && id == _request) setState(() => _result = r);
    } on ApiException catch (e) {
      if (mounted && id == _request) setState(() => _error = e.message);
    } catch (_) {
      if (mounted && id == _request) setState(() => _error = 'Something went wrong while finding a story.');
    } finally {
      if (mounted && id == _request) setState(() => _loading = false);
    }
  }

  Future<void> _pickMonth() async {
    final picked = await showDialog<(int, int)>(
      context: context,
      builder: (_) => _MonthPickerDialog(year: _year, month: _month, latest: _latestMonth),
    );
    if (picked != null) _setMonth(picked.$1, picked.$2);
  }

  Future<void> _searchCountry(List<Country> countries) async {
    final picked = await showSearch<Country?>(context: context, delegate: _CountrySearch(countries));
    if (picked != null) _select(picked);
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<Country>>(
      future: _countries,
      builder: (context, snap) {
        if (snap.hasError) {
          return Scaffold(body: ErrorView(message: 'Could not load the map data bundled with the app.'));
        }
        final countries = snap.data;
        return Scaffold(
          backgroundColor: const Color(0xFF07111F),
          appBar: AppBar(
            backgroundColor: Colors.transparent,
            foregroundColor: Colors.white,
            title: const Text('Explore the world'),
            actions: [
              if (countries != null)
                IconButton(
                  tooltip: 'Find a country',
                  icon: const Icon(Icons.search),
                  onPressed: () => _searchCountry(countries),
                ),
            ],
          ),
          body: countries == null
              ? const Center(child: CircularProgressIndicator())
              : Stack(children: [
                  const Positioned.fill(child: _Starfield()),
                  Column(children: [
                    _Controls(
                      year: _year,
                      month: _month,
                      category: _category,
                      canBack: !_atEarliest,
                      canForward: !_atLatest,
                      onShift: _shiftMonth,
                      onPickMonth: _pickMonth,
                      onCategory: _setCategory,
                    ),
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.only(bottom: 90),
                        child: GlobeView(
                          countries: countries,
                          month: _month,
                          selectedCode: _selected?.code,
                          onSelect: _select,
                        ),
                      ),
                    ),
                  ]),
                  DraggableScrollableSheet(
                    controller: _sheet,
                    initialChildSize: 0.16,
                    minChildSize: 0.12,
                    maxChildSize: 0.92,
                    snap: true,
                    snapSizes: const [0.16, 0.45],
                    builder: (context, scroll) => _ResultSheet(
                      scroll: scroll,
                      country: _selected,
                      year: _year,
                      month: _month,
                      categoryLabel: ExploreCategory.defaults.firstWhere((c) => c.id == _category).label,
                      loading: _loading,
                      error: _error,
                      result: _result,
                      repo: widget.repo,
                      onRetry: _load,
                    ),
                  ),
                ]),
        );
      },
    );
  }
}

// -----------------------------------------------------------------------------------------------
// Month and category controls.

class _Controls extends StatelessWidget {
  final int year, month;
  final String category;
  final bool canBack, canForward;
  final ValueChanged<int> onShift;
  final VoidCallback onPickMonth;
  final ValueChanged<String> onCategory;

  const _Controls({
    required this.year,
    required this.month,
    required this.category,
    required this.canBack,
    required this.canForward,
    required this.onShift,
    required this.onPickMonth,
    required this.onCategory,
  });

  @override
  Widget build(BuildContext context) {
    const white = Colors.white;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Column(children: [
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          IconButton(
            tooltip: 'Previous month',
            color: white,
            disabledColor: Colors.white24,
            onPressed: canBack ? () => onShift(-1) : null,
            icon: const Icon(Icons.chevron_left),
          ),
          TextButton.icon(
            style: TextButton.styleFrom(foregroundColor: white),
            onPressed: onPickMonth,
            icon: const Icon(Icons.calendar_month),
            label: Text('${monthNames[month - 1]} $year', style: const TextStyle(fontSize: 18)),
          ),
          IconButton(
            tooltip: 'Next month',
            color: white,
            disabledColor: Colors.white24,
            onPressed: canForward ? () => onShift(1) : null,
            icon: const Icon(Icons.chevron_right),
          ),
        ]),
        Text(seasonCaption(month), style: const TextStyle(color: Colors.white70, fontSize: 12)),
        const SizedBox(height: 8),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(children: [
            for (final c in ExploreCategory.defaults)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: ChoiceChip(
                  label: Text(c.label),
                  selected: c.id == category,
                  onSelected: (_) => onCategory(c.id),
                ),
              ),
          ]),
        ),
      ]),
    );
  }
}

class _MonthPickerDialog extends StatefulWidget {
  final int year, month;
  final (int, int) latest;
  const _MonthPickerDialog({required this.year, required this.month, required this.latest});

  @override
  State<_MonthPickerDialog> createState() => _MonthPickerDialogState();
}

class _MonthPickerDialogState extends State<_MonthPickerDialog> {
  late int _year = widget.year;

  @override
  Widget build(BuildContext context) {
    final (ly, lm) = widget.latest;
    return AlertDialog(
      title: Row(children: [
        IconButton(
          tooltip: 'Previous year',
          onPressed: _year > earliestYear ? () => setState(() => _year--) : null,
          icon: const Icon(Icons.chevron_left),
        ),
        Expanded(child: Text('$_year', textAlign: TextAlign.center)),
        IconButton(
          tooltip: 'Next year',
          onPressed: _year < ly ? () => setState(() => _year++) : null,
          icon: const Icon(Icons.chevron_right),
        ),
      ]),
      content: SizedBox(
        width: 300,
        child: GridView.count(
          shrinkWrap: true,
          crossAxisCount: 3,
          childAspectRatio: 2,
          mainAxisSpacing: 6,
          crossAxisSpacing: 6,
          children: [
            for (var m = 1; m <= 12; m++)
              Builder(builder: (context) {
                final enabled = _year < ly || (_year == ly && m <= lm);
                final current = _year == widget.year && m == widget.month;
                final label = Text(monthNames[m - 1].substring(0, 3));
                final onPressed = enabled ? () => Navigator.of(context).pop((_year, m)) : null;
                return current
                    ? FilledButton(onPressed: onPressed, child: label)
                    : OutlinedButton(onPressed: onPressed, child: label);
              }),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancel')),
        TextButton(onPressed: () => Navigator.of(context).pop(widget.latest), child: const Text('This month')),
      ],
    );
  }
}

class _CountrySearch extends SearchDelegate<Country?> {
  final List<Country> countries;
  _CountrySearch(this.countries) : super(searchFieldLabel: 'Country');

  @override
  List<Widget> buildActions(BuildContext context) =>
      [if (query.isNotEmpty) IconButton(tooltip: 'Clear', onPressed: () => query = '', icon: const Icon(Icons.clear))];

  @override
  Widget buildLeading(BuildContext context) =>
      BackButton(onPressed: () => close(context, null));

  @override
  Widget buildResults(BuildContext context) => buildSuggestions(context);

  @override
  Widget buildSuggestions(BuildContext context) {
    final q = query.trim().toLowerCase();
    final matches = countries.where((c) => q.isEmpty || c.name.toLowerCase().contains(q)).toList();
    if (matches.isEmpty) return const EmptyView(message: 'No country matches that name.');
    return ListView.builder(
      itemCount: matches.length,
      itemBuilder: (context, i) => ListTile(
        leading: const Icon(Icons.public),
        title: Text(matches[i].name),
        onTap: () => close(context, matches[i]),
      ),
    );
  }
}

// -----------------------------------------------------------------------------------------------
// Result sheet.

class _ResultSheet extends StatelessWidget {
  final ScrollController scroll;
  final Country? country;
  final int year, month;
  final String categoryLabel;
  final bool loading;
  final String? error;
  final Loaded<ExploreResult>? result;
  final NewsRepository repo;
  final VoidCallback onRetry;

  const _ResultSheet({
    required this.scroll,
    required this.country,
    required this.year,
    required this.month,
    required this.categoryLabel,
    required this.loading,
    required this.error,
    required this.result,
    required this.repo,
    required this.onRetry,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final c = country;
    final when = '${monthNames[month - 1]} $year · $categoryLabel';
    return Material(
      color: theme.colorScheme.surface,
      elevation: 8,
      borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
      clipBehavior: Clip.antiAlias,
      child: ListView(
        controller: scroll,
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Center(
            child: Container(
              width: 36,
              height: 4,
              decoration: BoxDecoration(color: theme.dividerColor, borderRadius: BorderRadius.circular(2)),
            ),
          ),
          const SizedBox(height: 10),
          if (c == null) ...[
            Text('Tap a country on the globe', style: theme.textTheme.titleMedium),
            const SizedBox(height: 4),
            Text('Drag to spin, pinch to zoom. Change the month to see the seasons move, '
                'then pick a country to get one story from there.', style: theme.textTheme.bodySmall),
          ] else ...[
            Text(c.name, style: theme.textTheme.titleLarge),
            Text(when, style: theme.textTheme.bodySmall),
            const SizedBox(height: 12),
            ..._content(context),
          ],
        ],
      ),
    );
  }

  List<Widget> _content(BuildContext context) {
    final theme = Theme.of(context);
    if (loading) {
      return [
        const LinearProgressIndicator(),
        const SizedBox(height: 12),
        Text(
          'Finding a story for ${country!.name}… The first visit to a country and month can take a little '
          'while because AI chooses the story. After that, everyone sees the same pick instantly.',
          style: theme.textTheme.bodySmall,
        ),
      ];
    }
    if (error != null) {
      return [
        Text(error!),
        const SizedBox(height: 8),
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton.icon(onPressed: onRetry, icon: const Icon(Icons.refresh), label: const Text('Try again')),
        ),
      ];
    }
    final loaded = result;
    if (loaded == null) return const [];
    final r = loaded.data;
    final pick = r.pick;
    final ai = r.ai;
    return [
      if (loaded.offline) ...[
        ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: OfflineBanner(syncedAt: loaded.syncedAt, reason: loaded.offlineReason),
        ),
        const SizedBox(height: 12),
      ],
      Wrap(spacing: 8, children: [
        Chip(
          avatar: Icon(r.isCurrentMonth ? Icons.bolt : Icons.history, size: 16),
          label: Text(r.isCurrentMonth ? 'This month' : 'From the archive'),
          visualDensity: VisualDensity.compact,
        ),
        if (r.cached)
          const Chip(
            avatar: Icon(Icons.people_outline, size: 16),
            label: Text('Same pick for every reader'),
            visualDensity: VisualDensity.compact,
          ),
      ]),
      const SizedBox(height: 8),
      if (pick != null) _PickCard(pick: pick),
      if (r.status != 'ok' && r.message != null) ...[
        const SizedBox(height: 12),
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(pick == null ? Icons.info_outline : Icons.warning_amber, size: 20, color: theme.hintColor),
          const SizedBox(width: 8),
          Expanded(child: Text(r.message!)),
        ]),
      ],
      if (ai != null && ai.reason.isNotEmpty) ...[
        const SizedBox(height: 12),
        AiBox(
          label: ai.label,
          generatedAt: ai.generatedAt,
          disclosure: ai.disclosure,
          children: [
            Text('Why this story', style: theme.textTheme.titleSmall),
            const SizedBox(height: 4),
            Text(ai.reason),
            if (ai.intro != null) ...[
              const SizedBox(height: 8),
              Text(ai.intro!),
            ],
          ],
        ),
      ],
      if (r.canRetry && !loaded.offline) ...[
        const SizedBox(height: 8),
        Align(
          alignment: Alignment.centerLeft,
          child: OutlinedButton.icon(onPressed: onRetry, icon: const Icon(Icons.refresh), label: const Text('Try again')),
        ),
      ],
      if (r.isCurrentMonth) ...[
        const SectionTitle('Grouped coverage from the last 7 days'),
        if (r.liveStories.isEmpty)
          Text('No grouped stories mentioning ${r.countryName.isEmpty ? country!.name : r.countryName} right now.',
              style: theme.textTheme.bodySmall)
        else
          for (final s in r.liveStories) StoryTile(story: s, repo: repo),
      ],
      const SizedBox(height: 16),
      if (r.sources.isNotEmpty) ...[
        _SourcesList(sources: r.sources),
        const SizedBox(height: 8),
      ],
      if (r.archiveNote.isNotEmpty) Text(r.archiveNote, style: theme.textTheme.bodySmall),
      if (r.attribution.isNotEmpty) ...[
        const SizedBox(height: 4),
        Text(r.attribution, style: theme.textTheme.labelSmall?.copyWith(color: theme.hintColor)),
      ],
    ];
  }
}

class _PickCard extends StatelessWidget {
  final ExplorePick pick;
  const _PickCard({required this.pick});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final meta = [
      pick.publisher,
      if (pick.section != null) pick.section!,
      if (pick.publishedAt != null) formatLocal(pick.publishedAt),
    ].join(' · ');
    return Card(
      margin: EdgeInsets.zero,
      clipBehavior: Clip.antiAlias,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (pick.thumbnail != null)
          AspectRatio(
            aspectRatio: 5 / 3,
            child: Image.network(
              pick.thumbnail!,
              fit: BoxFit.cover,
              errorBuilder: (_, _, _) => Container(
                color: theme.colorScheme.surfaceContainerHighest,
                alignment: Alignment.center,
                child: const Icon(Icons.image_not_supported_outlined),
              ),
            ),
          ),
        Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(meta, style: theme.textTheme.labelSmall),
            const SizedBox(height: 6),
            OriginalText(pick.headline, style: theme.textTheme.titleLarge),
            if (pick.trailText.isNotEmpty) ...[
              const SizedBox(height: 6),
              OriginalText(pick.trailText, attribution: 'Headline and summary from ${pick.publisher}'),
            ],
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: () => openLink(context, pick.url),
              icon: const Icon(Icons.open_in_new),
              label: Text('Read on ${pick.publisher}'),
            ),
          ]),
        ),
      ]),
    );
  }
}

/// Which publishers were searched for this pick and what each one returned.
class _SourcesList extends StatelessWidget {
  final List<ExploreSource> sources;
  const _SourcesList({required this.sources});

  static String _count(int n) => n == 1 ? '1 matching article' : '$n matching articles';

  static String _statusText(ExploreSource s) => switch (s.status) {
        'ok' => _count(s.candidates),
        'not_covered' => 'No archived feed for this month',
        'not_configured' => 'Not configured on the server',
        _ => s.candidates > 0 ? '${_count(s.candidates)} (archive only partly read)' : 'Unavailable right now',
      };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final matched = sources.where((s) => s.candidates > 0).length;
    return Theme(
      data: theme.copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: EdgeInsets.zero,
        dense: true,
        leading: const Icon(Icons.newspaper_outlined, size: 20),
        title: Text('Publishers searched', style: theme.textTheme.titleSmall),
        subtitle: Text('$matched of ${sources.length} had matching articles', style: theme.textTheme.bodySmall),
        children: [
          for (final s in sources)
            ListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              visualDensity: VisualDensity.compact,
              leading: Icon(
                switch (s.status) {
                  'ok' => s.candidates > 0 ? Icons.check_circle_outline : Icons.remove_circle_outline,
                  'not_covered' => Icons.remove_circle_outline,
                  _ => Icons.error_outline,
                },
                size: 18,
                color: s.status == 'ok' || s.status == 'not_covered' ? theme.hintColor : theme.colorScheme.error,
              ),
              title: Text(s.publisher),
              subtitle: Text([
                _statusText(s),
                if (s.via.isNotEmpty) 'via ${s.via}',
                if (s.status != 'ok' && s.detail != null) s.detail!,
              ].join(' · ')),
            ),
        ],
      ),
    );
  }
}

/// Static stars behind the globe.
class _Starfield extends StatelessWidget {
  const _Starfield();

  @override
  Widget build(BuildContext context) => CustomPaint(painter: _StarPainter());
}

class _StarPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = Colors.white54;
    // Deterministic pseudo-random positions so the sky doesn't flicker between frames.
    var seed = 7;
    double next() {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed / 0x7fffffff;
    }

    for (var i = 0; i < 120; i++) {
      canvas.drawCircle(Offset(next() * size.width, next() * size.height), next() * 1.2 + 0.2, paint);
    }
  }

  @override
  bool shouldRepaint(_StarPainter old) => false;
}
