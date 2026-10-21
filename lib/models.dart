// Data classes mirroring the My News API (server/app/views.py and server/app/explore.py).
// Parsing is tolerant: missing or null fields become empty values instead of throwing.

DateTime? parseTime(Object? v) =>
    v is String && v.isNotEmpty ? DateTime.tryParse(v)?.toUtc() : null;

String _s(Object? v) => v is String ? v : (v == null ? '' : v.toString());
String? _sn(Object? v) => v is String && v.isNotEmpty ? v : null;
int _i(Object? v) => v is int ? v : (v is num ? v.toInt() : int.tryParse(_s(v)) ?? 0);
List<Map<String, dynamic>> _maps(Object? v) =>
    v is List ? v.whereType<Map>().map((m) => m.cast<String, dynamic>()).toList() : const [];

class StorySummary {
  final String id;
  final String headline;
  final DateTime? latestPublishedAt;
  final int publisherCount;
  final List<String> publishers;
  final int articleCount;
  final String aiStatus;

  const StorySummary({
    required this.id,
    required this.headline,
    required this.latestPublishedAt,
    required this.publisherCount,
    required this.publishers,
    required this.articleCount,
    required this.aiStatus,
  });

  factory StorySummary.fromJson(Map<String, dynamic> j) => StorySummary(
        id: _s(j['id']),
        headline: _s(j['headline']),
        latestPublishedAt: parseTime(j['latest_published_at']),
        publisherCount: _i(j['publisher_count']),
        publishers: (j['publishers'] is List)
            ? (j['publishers'] as List).map(_s).toList()
            : const [],
        articleCount: _i(j['article_count']),
        aiStatus: _s(j['ai_status']),
      );
}

class StoryList {
  final List<StorySummary> stories;
  final int total;
  final int windowHours;
  final DateTime? lastSuccessfulCollectionAt;
  final DateTime? generatedAt;

  const StoryList({
    required this.stories,
    required this.total,
    required this.windowHours,
    required this.lastSuccessfulCollectionAt,
    required this.generatedAt,
  });

  factory StoryList.fromJson(Map<String, dynamic> j) => StoryList(
        stories: _maps(j['stories']).map(StorySummary.fromJson).toList(),
        total: _i(j['total']),
        windowHours: j['window_hours'] == null ? 168 : _i(j['window_hours']),
        lastSuccessfulCollectionAt: parseTime(j['last_successful_collection_at']),
        generatedAt: parseTime(j['generated_at']),
      );
}

class Article {
  final String id;
  final String title;
  final String publisherId;
  final String publisherName;
  final String url;
  final String excerpt;
  final DateTime? publishedAt;
  final bool dateEstimated;

  const Article({
    required this.id,
    required this.title,
    required this.publisherId,
    required this.publisherName,
    required this.url,
    required this.excerpt,
    required this.publishedAt,
    required this.dateEstimated,
  });

  factory Article.fromJson(Map<String, dynamic> j) => Article(
        id: _s(j['id']),
        title: _s(j['title']),
        publisherId: _s(j['publisher_id']),
        publisherName: _s(j['publisher_name']),
        url: _s(j['url']),
        excerpt: _s(j['excerpt']),
        publishedAt: parseTime(j['published_at']),
        dateEstimated: j['date_estimated'] == true,
      );
}

class BackgroundResource {
  final String title;
  final String url;
  final String description;
  final String source;
  final String label;

  const BackgroundResource({
    required this.title,
    required this.url,
    required this.description,
    required this.source,
    required this.label,
  });

  factory BackgroundResource.fromJson(Map<String, dynamic> j) => BackgroundResource(
        title: _s(j['title']),
        url: _s(j['url']),
        description: _s(j['description']),
        source: _s(j['source']),
        label: _sn(j['label']) ?? _s(j['title']),
      );
}

class SourceRef {
  final String articleId;
  final String publisherName;
  final String title;
  final String url;

  const SourceRef({
    required this.articleId,
    required this.publisherName,
    required this.title,
    required this.url,
  });

  factory SourceRef.fromJson(Map<String, dynamic> j) => SourceRef(
        articleId: _s(j['article_id']),
        publisherName: _s(j['publisher_name']),
        title: _s(j['title']),
        url: _s(j['url']),
      );
}

class Perspective {
  final String label;
  final String summary;
  final String evidenceLimits;
  final List<SourceRef> sources;

  const Perspective({
    required this.label,
    required this.summary,
    required this.evidenceLimits,
    required this.sources,
  });

  factory Perspective.fromJson(Map<String, dynamic> j) => Perspective(
        label: _s(j['label']),
        summary: _s(j['summary']),
        evidenceLimits: _s(j['evidence_limits']),
        sources: _maps(j['sources']).map(SourceRef.fromJson).toList(),
      );
}

class StoryAi {
  /// ok | pending | failed | outdated | updating
  final String status;
  final String label;
  final String disclosure;
  final String? message;
  final DateTime? generatedAt;
  final String? model;
  final String? promptVersion;
  final String? overview;
  final String? agreement;
  final String? disagreementOrUncertainty;
  final List<SourceRef> overviewSources;
  final String? perspectivesStatus;
  final List<Perspective> perspectives;
  final String? perspectivesMessage;

  const StoryAi({
    required this.status,
    required this.label,
    required this.disclosure,
    required this.message,
    required this.generatedAt,
    required this.model,
    required this.promptVersion,
    required this.overview,
    required this.agreement,
    required this.disagreementOrUncertainty,
    required this.overviewSources,
    required this.perspectivesStatus,
    required this.perspectives,
    required this.perspectivesMessage,
  });

  /// Text is shown only when the server says it is current (ok) or still valid but about to refresh (updating).
  bool get hasText => (status == 'ok' || status == 'updating') && (overview ?? '').isNotEmpty;

  factory StoryAi.fromJson(Map<String, dynamic> j) => StoryAi(
        status: _sn(j['status']) ?? 'pending',
        label: _sn(j['label']) ?? 'AI-generated',
        disclosure: _s(j['disclosure']),
        message: _sn(j['message']),
        generatedAt: parseTime(j['generated_at']),
        model: _sn(j['model']),
        promptVersion: _sn(j['prompt_version']),
        overview: _sn(j['overview']),
        agreement: _sn(j['agreement']),
        disagreementOrUncertainty: _sn(j['disagreement_or_uncertainty']),
        overviewSources: _maps(j['overview_sources']).map(SourceRef.fromJson).toList(),
        perspectivesStatus: _sn(j['perspectives_status']),
        perspectives: _maps(j['perspectives']).map(Perspective.fromJson).toList(),
        perspectivesMessage: _sn(j['perspectives_message']),
      );
}

class StoryDetail {
  final StorySummary summary;
  final List<Article> articles;
  final String backgroundStatus;
  final List<BackgroundResource> background;
  final String? backgroundMessage;
  final StoryAi ai;
  final DateTime? lastSuccessfulCollectionAt;

  const StoryDetail({
    required this.summary,
    required this.articles,
    required this.backgroundStatus,
    required this.background,
    required this.backgroundMessage,
    required this.ai,
    required this.lastSuccessfulCollectionAt,
  });

  factory StoryDetail.fromJson(Map<String, dynamic> j) {
    final bg = (j['background'] is Map) ? (j['background'] as Map).cast<String, dynamic>() : <String, dynamic>{};
    final ai = (j['ai'] is Map) ? (j['ai'] as Map).cast<String, dynamic>() : <String, dynamic>{};
    return StoryDetail(
      summary: StorySummary.fromJson(j),
      articles: _maps(j['articles']).map(Article.fromJson).toList(),
      backgroundStatus: _s(bg['status']),
      background: _maps(bg['resources']).map(BackgroundResource.fromJson).toList(),
      backgroundMessage: _sn(bg['message']),
      ai: StoryAi.fromJson(ai),
      lastSuccessfulCollectionAt: parseTime(j['last_successful_collection_at']),
    );
  }

  /// Articles grouped by publisher, preserving newest-first order inside each group.
  Map<String, List<Article>> get articlesByPublisher {
    final out = <String, List<Article>>{};
    for (final a in articles) {
      out.putIfAbsent(a.publisherName, () => []).add(a);
    }
    return out;
  }
}

class ExploreCategory {
  final String id;
  final String label;
  const ExploreCategory(this.id, this.label);

  static const defaults = [
    ExploreCategory('all', 'All'),
    ExploreCategory('feel_good', 'Feel good'),
    ExploreCategory('science', 'Science'),
    ExploreCategory('technology', 'Technology'),
  ];
}

class ExplorePick {
  final String headline;
  final String trailText;
  final String url;
  final String? thumbnail;
  final String? section;
  final String publisher;
  final DateTime? publishedAt;

  const ExplorePick({
    required this.headline,
    required this.trailText,
    required this.url,
    required this.thumbnail,
    required this.section,
    required this.publisher,
    required this.publishedAt,
  });

  factory ExplorePick.fromJson(Map<String, dynamic> j) => ExplorePick(
        headline: _s(j['headline']),
        trailText: _s(j['trail_text']),
        url: _s(j['url']),
        thumbnail: _sn(j['thumbnail']),
        section: _sn(j['section']),
        publisher: _sn(j['publisher']) ?? 'Unknown publisher',
        publishedAt: parseTime(j['published_at']),
      );
}

class ExploreAi {
  final String label;
  final String reason;
  final String? intro;
  final String? model;
  final DateTime? generatedAt;
  final String disclosure;

  const ExploreAi({
    required this.label,
    required this.reason,
    required this.intro,
    required this.model,
    required this.generatedAt,
    required this.disclosure,
  });

  factory ExploreAi.fromJson(Map<String, dynamic> j) => ExploreAi(
        label: _sn(j['label']) ?? 'AI-generated',
        reason: _s(j['reason']),
        intro: _sn(j['intro']),
        model: _sn(j['model']),
        generatedAt: parseTime(j['generated_at']),
        disclosure: _s(j['disclosure']),
      );
}

/// One publisher (or the local 7-day collection) searched for an Explore pick.
class ExploreSource {
  final String publisher;
  final String via;

  /// ok | not_configured | unavailable | not_covered
  final String status;
  final String? detail;
  final int candidates;

  const ExploreSource({
    required this.publisher,
    required this.via,
    required this.status,
    required this.detail,
    required this.candidates,
  });

  factory ExploreSource.fromJson(Map<String, dynamic> j) => ExploreSource(
        publisher: _s(j['publisher']),
        via: _s(j['via']),
        status: _sn(j['status']) ?? 'unavailable',
        detail: _sn(j['detail']),
        candidates: _i(j['candidates']),
      );

  Map<String, dynamic> toJson() =>
      {'publisher': publisher, 'via': via, 'status': status, 'detail': detail, 'candidates': candidates};
}

class ExploreResult {
  final String countryCode;
  final String countryName;
  final int year;
  final int month;
  final String monthLabel;
  final String categoryId;
  final String categoryLabel;
  final bool isCurrentMonth;

  /// ok | no_match | no_candidates | relevance_fallback | ai_unavailable | source_unavailable
  final String status;
  final String? message;
  final ExplorePick? pick;
  final ExploreAi? ai;
  final bool cached;
  final DateTime? generatedAt;
  final String attribution;
  final String archiveNote;
  final List<ExploreSource> sources;
  final List<StorySummary> liveStories;

  const ExploreResult({
    required this.countryCode,
    required this.countryName,
    required this.year,
    required this.month,
    required this.monthLabel,
    required this.categoryId,
    required this.categoryLabel,
    required this.isCurrentMonth,
    required this.status,
    required this.message,
    required this.pick,
    required this.ai,
    required this.cached,
    required this.generatedAt,
    required this.attribution,
    required this.archiveNote,
    this.sources = const [],
    required this.liveStories,
  });

  /// Temporary failures are not cached by the server, so the user can retry them.
  bool get canRetry => status == 'source_unavailable' || status == 'ai_unavailable' || status == 'relevance_fallback';

  factory ExploreResult.fromJson(Map<String, dynamic> j) {
    final country = (j['country'] is Map) ? (j['country'] as Map).cast<String, dynamic>() : <String, dynamic>{};
    final cat = (j['category'] is Map) ? (j['category'] as Map).cast<String, dynamic>() : <String, dynamic>{};
    return ExploreResult(
      countryCode: _s(country['code']),
      countryName: _s(country['name']),
      year: _i(j['year']),
      month: _i(j['month']),
      monthLabel: _s(j['month_label']),
      categoryId: _sn(cat['id']) ?? 'all',
      categoryLabel: _sn(cat['label']) ?? 'All',
      isCurrentMonth: j['is_current_month'] == true,
      status: _sn(j['status']) ?? 'source_unavailable',
      message: _sn(j['message']),
      pick: j['pick'] is Map ? ExplorePick.fromJson((j['pick'] as Map).cast<String, dynamic>()) : null,
      ai: j['ai'] is Map ? ExploreAi.fromJson((j['ai'] as Map).cast<String, dynamic>()) : null,
      cached: j['cached'] == true,
      generatedAt: parseTime(j['generated_at']),
      attribution: _s(j['attribution']),
      archiveNote: _s(j['archive_note']),
      sources: _maps(j['sources']).map(ExploreSource.fromJson).toList(),
      liveStories: _maps(j['live_stories']).map(StorySummary.fromJson).toList(),
    );
  }
}
