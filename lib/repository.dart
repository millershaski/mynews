import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'models.dart';

/// A value plus where it came from. When [offline] is true the data is the last synced copy.
class Loaded<T> {
  final T data;
  final bool offline;

  /// When this device last fetched the data from the server.
  final DateTime? syncedAt;
  final String? offlineReason;

  const Loaded(this.data, {this.offline = false, this.syncedAt, this.offlineReason});
}

/// Rolling retention window used by the server. Offline copies are filtered with the same rule,
/// so expired news never reappears just because the phone has no connection.
const Duration retentionWindow = Duration(hours: 168);

const _kList = 'cache.stories.v1';
const _kListSynced = 'cache.stories.synced_at';
const _kDetail = 'cache.story.v1.';
const _kExplore = 'cache.explore.v1.';

/// Fetches from the API and keeps a small offline copy in shared_preferences.
class NewsRepository {
  final ApiClient api;
  final Future<SharedPreferences> _prefs;
  final DateTime Function() _now;

  NewsRepository({ApiClient? api, Future<SharedPreferences>? prefs, DateTime Function()? now})
      : api = api ?? ApiClient(),
        _prefs = prefs ?? SharedPreferences.getInstance(),
        _now = now ?? DateTime.now;

  DateTime get _cutoff => _now().toUtc().subtract(retentionWindow);

  Future<DateTime?> lastSync() async {
    final p = await _prefs;
    return DateTime.tryParse(p.getString(_kListSynced) ?? '');
  }

  Future<Loaded<StoryList>> stories() async {
    final p = await _prefs;
    try {
      final j = await api.storiesJson();
      final now = _now().toUtc();
      await p.setString(_kList, jsonEncode(j));
      await p.setString(_kListSynced, now.toIso8601String());
      await _pruneDetails(p, StoryList.fromJson(j).stories.map((s) => s.id).toSet());
      return Loaded(StoryList.fromJson(j), syncedAt: now);
    } on ApiException catch (e) {
      final raw = p.getString(_kList);
      if (!e.network || raw == null) rethrow;
      final cached = StoryList.fromJson(jsonDecode(raw) as Map<String, dynamic>);
      return Loaded(
        filterExpired(cached, _cutoff),
        offline: true,
        syncedAt: DateTime.tryParse(p.getString(_kListSynced) ?? ''),
        offlineReason: e.message,
      );
    }
  }

  Future<Loaded<StoryDetail>> story(String id) async {
    final p = await _prefs;
    try {
      final j = await api.storyJson(id);
      final now = _now().toUtc();
      await p.setString('$_kDetail$id', jsonEncode({'synced_at': now.toIso8601String(), 'story': j}));
      return Loaded(StoryDetail.fromJson(j), syncedAt: now);
    } on ApiException catch (e) {
      if (e.statusCode == 404) await p.remove('$_kDetail$id');
      final raw = p.getString('$_kDetail$id');
      if (!e.network || raw == null) rethrow;
      final wrapped = jsonDecode(raw) as Map<String, dynamic>;
      final detail = filterExpiredDetail(
        StoryDetail.fromJson((wrapped['story'] as Map).cast<String, dynamic>()),
        _cutoff,
      );
      if (detail == null) {
        throw const ApiException(
          'You are offline and this story is older than 7 days, so it is no longer shown.',
          network: true,
        );
      }
      return Loaded(detail,
          offline: true, syncedAt: DateTime.tryParse('${wrapped['synced_at']}'), offlineReason: e.message);
    }
  }

  Future<Loaded<ExploreResult>> explore({
    required String country,
    required int year,
    required int month,
    required String category,
  }) async {
    final p = await _prefs;
    final key = '$_kExplore$country.$year.$month.$category';
    try {
      final r = await api.explore(country: country, year: year, month: month, category: category);
      final now = _now().toUtc();
      // Failures that carry no story are not kept offline; they should be retried, not replayed.
      if (r.status != 'source_unavailable' && r.status != 'ai_unavailable') {
        await p.setString(key, jsonEncode({'synced_at': now.toIso8601String(), 'raw': _exploreToRaw(r)}));
      }
      return Loaded(r, syncedAt: now);
    } on ApiException catch (e) {
      final raw = p.getString(key);
      if (!e.network || raw == null) rethrow;
      final wrapped = jsonDecode(raw) as Map<String, dynamic>;
      final r = ExploreResult.fromJson((wrapped['raw'] as Map).cast<String, dynamic>());
      return Loaded(
        filterExpiredExplore(r, _cutoff),
        offline: true,
        syncedAt: DateTime.tryParse('${wrapped['synced_at']}'),
        offlineReason: e.message,
      );
    }
  }

  Future<void> _pruneDetails(SharedPreferences p, Set<String> keep) async {
    for (final k in p.getKeys().where((k) => k.startsWith(_kDetail)).toList()) {
      if (!keep.contains(k.substring(_kDetail.length))) await p.remove(k);
    }
  }
}

// Explore results are re-serialised from the model so the cache only holds fields the app understands.
Map<String, dynamic> _exploreToRaw(ExploreResult r) => {
      'country': {'code': r.countryCode, 'name': r.countryName},
      'year': r.year,
      'month': r.month,
      'month_label': r.monthLabel,
      'category': {'id': r.categoryId, 'label': r.categoryLabel},
      'is_current_month': r.isCurrentMonth,
      'status': r.status,
      'message': r.message,
      'pick': r.pick == null
          ? null
          : {
              'headline': r.pick!.headline,
              'trail_text': r.pick!.trailText,
              'url': r.pick!.url,
              'thumbnail': r.pick!.thumbnail,
              'section': r.pick!.section,
              'publisher': r.pick!.publisher,
              'published_at': r.pick!.publishedAt?.toIso8601String(),
            },
      'ai': r.ai == null
          ? null
          : {
              'label': r.ai!.label,
              'reason': r.ai!.reason,
              'intro': r.ai!.intro,
              'model': r.ai!.model,
              'generated_at': r.ai!.generatedAt?.toIso8601String(),
              'disclosure': r.ai!.disclosure,
            },
      'cached': r.cached,
      'generated_at': r.generatedAt?.toIso8601String(),
      'attribution': r.attribution,
      'archive_note': r.archiveNote,
      'sources': [for (final s in r.sources) s.toJson()],
      'live_stories': [
        for (final s in r.liveStories)
          {
            'id': s.id,
            'headline': s.headline,
            'latest_published_at': s.latestPublishedAt?.toIso8601String(),
            'publisher_count': s.publisherCount,
            'publishers': s.publishers,
            'article_count': s.articleCount,
            'ai_status': s.aiStatus,
          }
      ],
    };

bool _expired(DateTime? t, DateTime cutoff) => t == null || t.isBefore(cutoff);

/// Drops stories whose newest article left the 7-day window. Order stays newest first.
StoryList filterExpired(StoryList list, DateTime cutoff) {
  final kept = list.stories.where((s) => !_expired(s.latestPublishedAt, cutoff)).toList()
    ..sort((a, b) => (b.latestPublishedAt ?? DateTime(0)).compareTo(a.latestPublishedAt ?? DateTime(0)));
  return StoryList(
    stories: kept,
    total: kept.length,
    windowHours: list.windowHours,
    lastSuccessfulCollectionAt: list.lastSuccessfulCollectionAt,
    generatedAt: list.generatedAt,
  );
}

/// Removes expired articles from an offline copy. Returns null when nothing is left.
/// If any article was dropped, the AI text is hidden because it may rely on that article.
StoryDetail? filterExpiredDetail(StoryDetail d, DateTime cutoff) {
  final kept = d.articles.where((a) => !_expired(a.publishedAt, cutoff)).toList();
  if (kept.isEmpty) return null;
  if (kept.length == d.articles.length) return d;
  final publishers = <String>[];
  for (final a in kept) {
    if (!publishers.contains(a.publisherName)) publishers.add(a.publisherName);
  }
  return StoryDetail(
    summary: StorySummary(
      id: d.summary.id,
      headline: d.summary.headline,
      latestPublishedAt: kept.first.publishedAt,
      publisherCount: publishers.length,
      publishers: publishers,
      articleCount: kept.length,
      aiStatus: d.summary.aiStatus,
    ),
    articles: kept,
    backgroundStatus: d.backgroundStatus,
    background: d.background,
    backgroundMessage: d.backgroundMessage,
    ai: StoryAi(
      status: 'outdated',
      label: d.ai.label,
      disclosure: d.ai.disclosure,
      message: 'Some articles behind this AI overview are now older than 7 days, so it is hidden until the '
          'app can sync again.',
      generatedAt: null,
      model: null,
      promptVersion: null,
      overview: null,
      agreement: null,
      disagreementOrUncertainty: null,
      overviewSources: const [],
      perspectivesStatus: null,
      perspectives: const [],
      perspectivesMessage: null,
    ),
    lastSuccessfulCollectionAt: d.lastSuccessfulCollectionAt,
  );
}

ExploreResult filterExpiredExplore(ExploreResult r, DateTime cutoff) => ExploreResult(
      countryCode: r.countryCode,
      countryName: r.countryName,
      year: r.year,
      month: r.month,
      monthLabel: r.monthLabel,
      categoryId: r.categoryId,
      categoryLabel: r.categoryLabel,
      isCurrentMonth: r.isCurrentMonth,
      status: r.status,
      message: r.message,
      pick: r.pick,
      ai: r.ai,
      cached: r.cached,
      generatedAt: r.generatedAt,
      attribution: r.attribution,
      archiveNote: r.archiveNote,
      sources: r.sources,
      liveStories: r.liveStories.where((s) => !_expired(s.latestPublishedAt, cutoff)).toList(),
    );
