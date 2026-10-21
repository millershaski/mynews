import 'package:flutter/material.dart';

import '../api.dart';
import '../repository.dart';
import '../widgets/common.dart';

/// Server health, collection times, data sources and the app's disclosures.
class AboutScreen extends StatefulWidget {
  final NewsRepository repo;
  const AboutScreen({super.key, required this.repo});

  @override
  State<AboutScreen> createState() => _AboutScreenState();
}

class _AboutScreenState extends State<AboutScreen> {
  Map<String, dynamic>? _status;
  DateTime? _lastSync;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final sync = await widget.repo.lastSync();
    try {
      final s = await widget.repo.api.status();
      if (mounted) setState(() => _status = s);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (_) {
      if (mounted) setState(() => _error = 'Could not read the server status.');
    } finally {
      if (mounted) {
        setState(() {
          _lastSync = sync;
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final s = _status;
    return Scaffold(
      appBar: AppBar(
        title: const Text('About & status'),
        actions: [
          IconButton(tooltip: 'Refresh', onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
          children: [
            if (_loading) const LinearProgressIndicator(),
            const SectionTitle('On this device'),
            _Row('Last successful sync', _lastSync == null ? 'Never' : formatLocal(_lastSync)),
            const SizedBox(height: 4),
            Text(
              'Stories and explore picks you have opened are saved for offline reading. Offline, the app '
              'still hides anything older than 7 days, and publisher pages need an internet connection.',
              style: theme.textTheme.bodySmall,
            ),
            const SectionTitle('Server'),
            if (s == null)
              Text(_error ?? 'Loading…')
            else ..._server(context, s),
            const SectionTitle('How this app uses AI'),
            const Text(
              '• Story overviews and perspectives are written by AI from the publishers\' RSS feed excerpts only, '
              'never full articles. They are generated once on the server, shared with every reader, and '
              'regenerated only when a story\'s articles change.\n'
              '• Every AI claim lists the articles it is based on. AI text is always shown in a tinted box '
              'labelled "AI-generated" with the time it was generated; original publisher wording is shown in serif type.\n'
              '• On the globe, AI chooses one archive article for each country, month and category. '
              'The choice is cached and everyone sees the same pick.\n'
              '• If the AI service fails, the app says so. It never shows made-up text.',
            ),
            const SectionTitle('Sources'),
            const Text(
              'Latest stories come from public RSS feeds of several publishers, grouped automatically. '
              'Background links come from Wikipedia. Globe picks are drawn from the same eight publishers: '
              'The Guardian through The Guardian Open Platform (powered by The Guardian), and BBC News, NPR, '
              'Fox News, CBS News, The New York Times, Al Jazeera and ABC News through copies of their RSS feeds '
              'saved by the Internet Archive\'s Wayback Machine. For the current month, articles collected in the '
              'last 7 days are included too. Archive coverage varies by publisher and month. '
              'All articles open on the publisher\'s own site.',
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _server(BuildContext context, Map<String, dynamic> s) {
    final theme = Theme.of(context);
    DateTime? t(Object? v) => v is String ? DateTime.tryParse(v) : null;
    final ai = (s['ai'] as Map?)?.cast<String, dynamic>() ?? const {};
    final lastRun = (s['last_run'] as Map?)?.cast<String, dynamic>();
    final feeds = (s['feeds'] as List? ?? const []).whereType<Map>().map((m) => m.cast<String, dynamic>()).toList();
    final archive = (s['archive_sources'] as Map?)?.cast<String, dynamic>() ?? const {};
    final wayback = (archive['wayback_publishers'] as List? ?? const []).map((e) => '$e').toList();
    return [
      _Row('Last successful collection', formatLocal(t(s['last_successful_collection_at']))),
      _Row('Last collection run',
          lastRun == null ? 'None yet' : '${formatLocal(t(lastRun['started_at']))} · ${lastRun['status']}'),
      _Row('Retention window', '${s['window_hours']} hours'),
      _Row('Articles / stories', '${s['articles']} / ${s['stories']}'),
      _Row('AI summaries / globe picks', '${s['summaries']} / ${s['explore_picks']}'),
      _Row('AI', ai['configured'] == true ? '${ai['model']}' : 'Not configured'),
      if (ai['configured'] == true) _Row('AI calls today (UTC)', '${ai['calls_today_utc']} of ${ai['daily_limit']}'),
      _Row('Globe: The Guardian API', archive['guardian_api'] == 'configured' ? 'Configured' : 'Not configured'),
      _Row('Globe: Wayback Machine feeds', wayback.isEmpty ? 'None' : wayback.join(', ')),
      const SizedBox(height: 12),
      Text('Feeds', style: theme.textTheme.titleSmall),
      for (final f in feeds)
        ListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          leading: Icon(
            f['last_error'] == null ? Icons.check_circle_outline : Icons.error_outline,
            color: f['last_error'] == null ? Colors.green : theme.colorScheme.error,
          ),
          title: Text('${f['name']}'),
          subtitle: Text(
            'Last success: ${formatLocal(t(f['last_success_at']))} · ${f['last_item_count']} items'
            '${f['last_error'] == null ? '' : '\nLast error: ${f['last_error']}'}',
          ),
        ),
    ];
  }
}

class _Row extends StatelessWidget {
  final String label, value;
  const _Row(this.label, this.value);

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(flex: 5, child: Text(label, style: Theme.of(context).textTheme.bodySmall)),
          Expanded(flex: 6, child: Text(value)),
        ]),
      );
}
