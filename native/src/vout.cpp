// The Qt half of Refrata's native video output (refrata_native, lib.rs is the other half).
//
// libVLC decodes into memory from lib.rs's pool; for every displayed picture lib.rs calls
// rv_deliver (decoder thread). Here the picture becomes a QVideoFrame over that very memory (a
// QAbstractVideoBuffer that gives the memory back to the pool when Qt is done with it: no copy, no
// allocation per frame) and reaches the QVideoSinks of the screen without any Python:
//
//   decoder thread  rv_deliver: the frame and the time it was displayed (wall clock, ms: the same
//                   clock as QML's Date.now()) queued, one event posted to the Router (if none is
//                   pending already). A busy interface takes the queue in order (up to 3 pictures):
//                   smooth motion needs every picture, a skipped one made it interpolate across two
//   UI thread       Router::event: the frame goes to every sink, or, with smooth motion, to the
//                   two sinks in turn, and VideoPipeline.frameArrived(slot) is called
//
// Sinks and the pipeline item are only ever touched on the UI thread, where QML deletes them, so a
// sink that goes away is simply skipped (QPointer).
#include <QtCore/QCoreApplication>
#include <QtCore/QDateTime>
#include <QtCore/QEvent>
#include <QtCore/QMetaObject>
#include <QtCore/QMutex>
#include <QtCore/QObject>
#include <QtCore/QPointer>
#include <QtCore/QRect>
#include <QtCore/QVariant>
#include <QtMultimedia/QAbstractVideoBuffer>
#include <QtMultimedia/QVideoFrame>
#include <QtMultimedia/QVideoFrameFormat>
#include <QtMultimedia/QVideoSink>
#include "rtx.h"
#include "vout.h"
#include <QtQml/qqml.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <deque>
#include <memory>
#include <vector>

extern "C" void rv_buffer_release(void *pool, unsigned long long handle);   // lib.rs

namespace {

struct Planes {
    int count = 0;
    unsigned char *data[4] = {};
    int pitch[4] = {};
    int size[4] = {};
};

class PoolBuffer : public QAbstractVideoBuffer {
public:
    PoolBuffer(void *pool, unsigned long long handle, const Planes &p, const QVideoFrameFormat &f)
        : m_pool(pool), m_handle(handle), m_planes(p), m_format(f) {}
    ~PoolBuffer() override { rv_buffer_release(m_pool, m_handle); }
    MapData map(QVideoFrame::MapMode) override {
        MapData d;
        d.planeCount = m_planes.count;
        for (int i = 0; i < m_planes.count; ++i) {
            d.data[i] = m_planes.data[i];
            d.bytesPerLine[i] = m_planes.pitch[i];
            d.dataSize[i] = m_planes.size[i];
        }
        return d;
    }
    QVideoFrameFormat format() const override { return m_format; }

private:
    void *m_pool;
    unsigned long long m_handle;
    Planes m_planes;
    QVideoFrameFormat m_format;
};

const QEvent::Type FrameEvent = QEvent::Type(QEvent::User + 0x5246);

class Router : public QObject {
public:
    QMutex lock;                       // guards format, queue, pending
    QVideoFrameFormat format;
    struct Stamped { QVideoFrame frame; double ms; };
    std::deque<Stamped> queue;         // displayed by libVLC, not yet shown (oldest first)
    static constexpr size_t QueueMax = 3;
    bool pending = false;
    std::atomic<unsigned long long> delivered{0};   // pictures handed to the sinks (UI thread)
    std::atomic<unsigned long long> skipped{0};     // dropped: the queue was full before the UI took them

    // UI thread only
    std::vector<QPointer<QVideoSink>> sinks;
    QPointer<QVideoSink> pairA, pairB;
    QPointer<QObject> pipeline;        // VideoPipeline.qml: frameArrived(slot)
    int slot = 0;
    QVideoFrame last;                  // the picture on screen (new sinks, snapshots)
    quint64 seq = 0;                   // bumped with every new picture
    std::vector<QPointer<RtxVideo>> viewers;   // RtxVideo items drawing the picture themselves
    int recent[32] = {};               // the last pair slots used, for tests
    unsigned long long recentCount = 0;
    long long times[512] = {};         // when the last pictures reached the sinks (us, steady clock)
    unsigned long long timesCount = 0;

    bool event(QEvent *e) override {
        if (e->type() != FrameEvent)
            return QObject::event(e);
        std::deque<Stamped> got;
        {
            QMutexLocker l(&lock);
            got.swap(queue);
            pending = false;
        }
        for (const auto &g : got)
            if (g.frame.isValid())
                show(g.frame, g.ms);
        return true;
    }

    void show(const QVideoFrame &f, double ms) {
        last = f;
        ++seq;
        for (auto &v : viewers)
            if (v)
                v->pictureArrived();
        if (pairA && pairB) {
            const int s = slot;
            (s ? pairB : pairA)->setVideoFrame(f);
            slot = s ^ 1;
            recent[recentCount++ % 32] = s;
            if (pipeline) {
                // the stamp as a property, the call with one argument: a second QVariant argument
                // never reached the QML function (frameArrived was not called at all)
                pipeline->setProperty("frameStamp", ms);
                QMetaObject::invokeMethod(pipeline.data(), "frameArrived", Q_ARG(QVariant, QVariant(s)));
            }
        }
        for (auto &k : sinks)
            if (k && k != pairA && k != pairB)
                k->setVideoFrame(f);
        times[timesCount++ % 512] = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
        delivered.fetch_add(1, std::memory_order_relaxed);
    }
};

} // namespace

extern "C" {

// UI thread: a router living there
void *rv_router_new() { return new Router(); }

void rv_router_free(void *r) {
    if (r)
        static_cast<Router *>(r)->deleteLater();
}

// pixfmt: 0 NV12, 1 YUV420P, 2 P010. cs: 0 BT.601, 1 BT.709, 2 BT.2020. viewport 0 = whole frame
void rv_set_format(void *r, int w, int h, int pixfmt, int cs, int full, int vw, int vh) {
    if (!r)
        return;
    auto *rt = static_cast<Router *>(r);
    const QVideoFrameFormat::PixelFormat pf = pixfmt == 2 ? QVideoFrameFormat::Format_P010
        : pixfmt == 1 ? QVideoFrameFormat::Format_YUV420P : QVideoFrameFormat::Format_NV12;
    QVideoFrameFormat f(QSize(w, h), pf);
    f.setColorSpace(cs == 2 ? QVideoFrameFormat::ColorSpace_BT2020
                    : cs == 1 ? QVideoFrameFormat::ColorSpace_BT709 : QVideoFrameFormat::ColorSpace_BT601);
    f.setColorRange(full ? QVideoFrameFormat::ColorRange_Full : QVideoFrameFormat::ColorRange_Video);
    if (vw > 0 && vh > 0 && (vw != w || vh != h))
        f.setViewport(QRect(0, 0, vw, vh));
    QMutexLocker l(&rt->lock);
    rt->format = f;
}

// decoder thread: one displayed picture. Returns 0 when there is no format (the buffer is released)
int rv_deliver(void *r, void *pool, unsigned long long handle, int planes, unsigned char **data,
               const int *pitch, const int *size) {
    auto *rt = static_cast<Router *>(r);
    Planes p;
    p.count = planes < 4 ? planes : 4;
    for (int i = 0; i < p.count; ++i) {
        p.data[i] = data[i];
        p.pitch[i] = pitch[i];
        p.size[i] = size[i];
    }
    bool post = false;
    {
        QMutexLocker l(&rt->lock);
        if (!rt->format.isValid()) {
            l.unlock();
            rv_buffer_release(pool, handle);
            return 0;
        }
        QVideoFrame f(std::make_unique<PoolBuffer>(pool, handle, p, rt->format));
        while (rt->queue.size() >= Router::QueueMax) {
            rt->queue.pop_front();
            rt->skipped.fetch_add(1, std::memory_order_relaxed);
        }
        rt->queue.push_back({f, double(QDateTime::currentMSecsSinceEpoch())});
        post = !rt->pending;
        rt->pending = true;
    }
    if (post)
        QCoreApplication::postEvent(rt, new QEvent(FrameEvent), Qt::HighEventPriority);
    return 1;
}

// UI thread: where the pictures go
void rv_set_sinks(void *r, void **sinks, int n) {
    if (!r)
        return;
    auto *rt = static_cast<Router *>(r);
    rt->sinks.clear();
    for (int i = 0; i < n; ++i)
        rt->sinks.emplace_back(static_cast<QVideoSink *>(sinks[i]));
    if (rt->last.isValid())
        for (int i = 0; i < n; ++i)
            static_cast<QVideoSink *>(sinks[i])->setVideoFrame(rt->last);
}

void rv_set_pair(void *r, void *a, void *b, void *pipeline) {
    if (!r)
        return;
    auto *rt = static_cast<Router *>(r);
    rt->pairA = static_cast<QVideoSink *>(a);
    rt->pairB = static_cast<QVideoSink *>(b);
    rt->pipeline = static_cast<QObject *>(pipeline);
    rt->slot = 0;
    if (a && b && rt->last.isValid()) {
        rt->pairA->setVideoFrame(rt->last);
        rt->pairB->setVideoFrame(rt->last);
        rt->slot = 1;
        if (rt->pipeline) {
            rt->pipeline->setProperty("frameStamp", double(QDateTime::currentMSecsSinceEpoch()));
            QMetaObject::invokeMethod(rt->pipeline.data(), "frameArrived", Q_ARG(QVariant, QVariant(0)));
        }
    }
}

// UI thread: forget the picture on screen (a new item starts)
void rv_clear(void *r) {
    if (!r)
        return;
    auto *rt = static_cast<Router *>(r);
    rt->last = QVideoFrame();
    QMutexLocker l(&rt->lock);
    rt->queue.clear();
}

// UI thread: a copy of the picture on screen (delete with rv_frame_free), or null
void *rv_last_frame(void *r) {
    if (!r)
        return nullptr;
    auto *rt = static_cast<Router *>(r);
    return rt->last.isValid() ? new QVideoFrame(rt->last) : nullptr;
}

void rv_frame_free(void *f) { delete static_cast<QVideoFrame *>(f); }

unsigned long long rv_delivered(void *r) { return r ? static_cast<Router *>(r)->delivered.load() : 0; }
unsigned long long rv_skipped(void *r) { return r ? static_cast<Router *>(r)->skipped.load() : 0; }

int rv_recent_slots(void *r, int *out, int n) {
    if (!r)
        return 0;
    auto *rt = static_cast<Router *>(r);
    const unsigned long long have = rt->recentCount < 32 ? rt->recentCount : 32;
    const int k = n < int(have) ? n : int(have);
    for (int i = 0; i < k; ++i)
        out[i] = rt->recent[(rt->recentCount - k + i) % 32];
    return k;
}

// UI thread: when the last n pictures reached the sinks (us), oldest first
int rv_recent_times(void *r, long long *out, int n) {
    if (!r)
        return 0;
    auto *rt = static_cast<Router *>(r);
    const unsigned long long have = rt->timesCount < 512 ? rt->timesCount : 512;
    const int k = n < int(have) ? n : int(have);
    for (int i = 0; i < k; ++i)
        out[i] = rt->times[(rt->timesCount - k + i) % 512];
    return k;
}

// UI thread: the QML type of the RTX video item (rtx.cpp)
void rv_register_qml() {
    qmlRegisterType<RtxVideo>("Refrata.Native", 1, 0, "RtxVideo");
}

} // extern "C"

QVideoFrame rv_last(void *r, quint64 *seq) {
    auto *rt = static_cast<Router *>(r);
    if (seq)
        *seq = rt->seq;
    return rt->last;
}

void rv_add_viewer(void *r, RtxVideo *item) {
    static_cast<Router *>(r)->viewers.emplace_back(item);
}

void rv_remove_viewer(void *r, RtxVideo *item) {
    auto &v = static_cast<Router *>(r)->viewers;
    v.erase(std::remove_if(v.begin(), v.end(), [item](const QPointer<RtxVideo> &p) { return !p || p.data() == item; }), v.end());
}
