// RtxVideo (rtx.h): the picture on screen made by the GPU's video processor (D3D11) instead of a
// shader, so the driver's own enhancements apply. On NVIDIA RTX GPUs:
//
//   RTX Video Super Resolution  the picture is upscaled to the window by the driver's AI model
//   RTX Video HDR (TrueHDR)     SDR video is turned into HDR10 for an HDR screen
//
// Both are switched on per stream with ID3D11VideoContext::VideoProcessorSetStreamExtension and
// NVIDIA's interface GUIDs (the same calls as Chromium and mpv's d3d11vpp filter).
//
// Each new picture (from vout.cpp's router, on the UI thread) is uploaded to an NV12 / P010
// texture on Qt Quick's own D3D11 device, processed straight to the size it has on screen, and the
// output texture is drawn by the scene graph. Everything here runs on the render thread, while the
// UI thread waits in the scene graph's sync, so the router is read safely.
#include "rtx.h"
#include "vout.h"

#include <QtCore/QRunnable>
#include <QtGui/rhi/qrhi.h>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGSimpleTextureNode>
#include <QtQuick/QSGTexture>
#include <QtMultimedia/QVideoFrameFormat>
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <vector>

using Microsoft::WRL::ComPtr;

// NVIDIA's video processing extensions (mpv video/filter/vf_d3d11vpp.c)
static const GUID NV_PPE = {0xd43ce1b3, 0x1f4b, 0x48ac, {0xba, 0xee, 0xc3, 0xc2, 0x53, 0x75, 0xe6, 0xf7}};
static const GUID NV_TRUEHDR = {0xfdd62bb4, 0x620b, 0x4fd7, {0x9a, 0xb3, 0x1e, 0x59, 0xd0, 0xd5, 0x44, 0xb3}};

struct RtxState {
    ID3D11Device *dev = nullptr;               // Qt's (not owned)
    ID3D11DeviceContext *ctx = nullptr;
    ComPtr<ID3D11VideoDevice> vdev;
    ComPtr<ID3D11VideoContext> vctx;
    ComPtr<ID3D11VideoContext1> vctx1;
    bool nvidia = false;
    // input
    ComPtr<ID3D11Texture2D> in;
    ComPtr<ID3D11VideoProcessorInputView> inView;
    DXGI_FORMAT inFmt = DXGI_FORMAT_UNKNOWN;
    QSize inSize;
    std::vector<unsigned char> staging;
    // processor and output
    ComPtr<ID3D11VideoProcessorEnumerator> en;
    ComPtr<ID3D11VideoProcessor> vp;
    ComPtr<ID3D11Texture2D> out;
    ComPtr<ID3D11VideoProcessorOutputView> outView;
    QSize outSize;
    bool outHdr = false;
    QRhiTexture *rhiTex = nullptr;
    QSGTexture *sgTex = nullptr;
    quint64 lastSeq = ~0ull;
    QString status;
    UINT frameIndex = 0;

    void dropOutput() {
        delete sgTex;              // owns rhiTex (QQuickWindow::createTextureFromRhiTexture)
        sgTex = nullptr;
        rhiTex = nullptr;
        outView.Reset();
        out.Reset();
        vp.Reset();
        en.Reset();
    }
    ~RtxState() { dropOutput(); }
};

RtxVideo::RtxVideo(QQuickItem *parent) : QQuickItem(parent) {
    setFlag(ItemHasContents, true);
}

RtxVideo::~RtxVideo() {
    if (m_router)
        rv_remove_viewer(reinterpret_cast<void *>(m_router), this);
    // the GPU objects belong to the render thread: normally releaseResources() handed them over
    // already; if not (the window is gone too) they can only be dropped with the device
    if (m_s && window())
        releaseResources();
}

void RtxVideo::setRouter(qulonglong r) {
    if (r == m_router)
        return;
    if (m_router)
        rv_remove_viewer(reinterpret_cast<void *>(m_router), this);
    m_router = r;
    if (m_router)
        rv_add_viewer(reinterpret_cast<void *>(m_router), this);
    Q_EMIT routerChanged();
    update();
}

void RtxVideo::setSuperResolution(bool on) {
    if (on != m_vsr) {
        m_vsr = on;
        if (m_s)
            m_s->lastSeq = ~0ull;
        Q_EMIT settingsChanged();
        update();
    }
}

void RtxVideo::setTrueHdr(bool on) {
    if (on != m_hdr) {
        m_hdr = on;
        if (m_s)
            m_s->lastSeq = ~0ull;
        Q_EMIT settingsChanged();
        update();
    }
}

QString RtxVideo::status() const { return m_status; }
QSize RtxVideo::sourceSize() const { return m_src; }
QSize RtxVideo::outputSize() const { return m_out; }
int RtxVideo::frames() const { return m_frames; }

void RtxVideo::releaseResources() {
    // the window's graphics go away (or the item leaves it): textures must go on the render thread
    class Cleanup : public QRunnable {
    public:
        explicit Cleanup(RtxState *s) : s(s) {}
        void run() override { delete s; }
        RtxState *s;
    };
    if (m_s && window()) {
        window()->scheduleRenderJob(new Cleanup(m_s), QQuickWindow::BeforeSynchronizingStage);
        m_s = nullptr;
    }
}

static bool init(RtxState *s, QQuickWindow *w) {
    QRhi *rhi = w->rhi();
    if (!rhi || rhi->backend() != QRhi::D3D11) {
        s->status = QStringLiteral("unsupported: not Direct3D 11");
        return false;
    }
    auto *h = static_cast<const QRhiD3D11NativeHandles *>(rhi->nativeHandles());
    s->dev = static_cast<ID3D11Device *>(h->dev);
    s->ctx = static_cast<ID3D11DeviceContext *>(h->context);
    if (FAILED(s->dev->QueryInterface(IID_PPV_ARGS(&s->vdev))) || FAILED(s->ctx->QueryInterface(IID_PPV_ARGS(&s->vctx)))) {
        s->status = QStringLiteral("unsupported: no video processor");
        return false;
    }
    s->ctx->QueryInterface(IID_PPV_ARGS(&s->vctx1));
    ComPtr<IDXGIDevice> dxgi;
    ComPtr<IDXGIAdapter> ad;
    DXGI_ADAPTER_DESC desc = {};
    if (SUCCEEDED(s->dev->QueryInterface(IID_PPV_ARGS(&dxgi))) && SUCCEEDED(dxgi->GetAdapter(&ad)) && SUCCEEDED(ad->GetDesc(&desc)))
        s->nvidia = desc.VendorId == 0x10DE;
    return true;
}

// the picture as one contiguous NV12 / P010 buffer (UpdateSubresource takes the chroma right after
// the luma, at the same pitch)
static bool upload(RtxState *s, QVideoFrame &f) {
    const QVideoFrameFormat::PixelFormat pf = f.pixelFormat();
    const bool deep = pf == QVideoFrameFormat::Format_P010 || pf == QVideoFrameFormat::Format_P016;
    if (pf != QVideoFrameFormat::Format_NV12 && pf != QVideoFrameFormat::Format_YUV420P && !deep)
        return false;
    const QSize sz(f.width() & ~1, f.height() & ~1);
    const DXGI_FORMAT fmt = deep ? DXGI_FORMAT_P010 : DXGI_FORMAT_NV12;
    if (!s->in || s->inSize != sz || s->inFmt != fmt) {
        s->in.Reset();
        s->inView.Reset();
        D3D11_TEXTURE2D_DESC td = {};
        td.Width = sz.width();
        td.Height = sz.height();
        td.MipLevels = 1;
        td.ArraySize = 1;
        td.Format = fmt;
        td.SampleDesc.Count = 1;
        td.Usage = D3D11_USAGE_DEFAULT;
        if (FAILED(s->dev->CreateTexture2D(&td, nullptr, &s->in)))
            return false;
        s->inSize = sz;
        s->inFmt = fmt;
        s->en.Reset();   // the processor is made for the input size
    }
    if (!f.map(QVideoFrame::ReadOnly))
        return false;
    const int bpp = deep ? 2 : 1;
    const int pitch = sz.width() * bpp;
    const int h = sz.height();
    s->staging.resize(size_t(pitch) * h * 3 / 2);
    unsigned char *dst = s->staging.data();
    for (int y = 0; y < h; ++y)
        memcpy(dst + size_t(y) * pitch, f.bits(0) + size_t(y) * f.bytesPerLine(0), pitch);
    unsigned char *uv = dst + size_t(pitch) * h;
    if (pf == QVideoFrameFormat::Format_YUV420P) {
        // three planes: interleave U and V into NV12's second plane
        for (int y = 0; y < h / 2; ++y) {
            const unsigned char *u = f.bits(1) + size_t(y) * f.bytesPerLine(1);
            const unsigned char *v = f.bits(2) + size_t(y) * f.bytesPerLine(2);
            unsigned char *o = uv + size_t(y) * pitch;
            for (int x = 0; x < sz.width() / 2; ++x) {
                o[2 * x] = u[x];
                o[2 * x + 1] = v[x];
            }
        }
    } else {
        for (int y = 0; y < h / 2; ++y)
            memcpy(uv + size_t(y) * pitch, f.bits(1) + size_t(y) * f.bytesPerLine(1), pitch);
    }
    f.unmap();
    s->ctx->UpdateSubresource(s->in.Get(), 0, nullptr, s->staging.data(), pitch, 0);
    return true;
}

static DXGI_COLOR_SPACE_TYPE inputSpace(const QVideoFrameFormat &fmt) {
    const bool full = fmt.colorRange() == QVideoFrameFormat::ColorRange_Full;
    switch (fmt.colorSpace()) {
    case QVideoFrameFormat::ColorSpace_BT2020:
        return full ? DXGI_COLOR_SPACE_YCBCR_FULL_G22_LEFT_P2020 : DXGI_COLOR_SPACE_YCBCR_STUDIO_G22_LEFT_P2020;
    case QVideoFrameFormat::ColorSpace_BT601:
        return full ? DXGI_COLOR_SPACE_YCBCR_FULL_G22_LEFT_P601 : DXGI_COLOR_SPACE_YCBCR_STUDIO_G22_LEFT_P601;
    default:
        return full ? DXGI_COLOR_SPACE_YCBCR_FULL_G22_LEFT_P709 : DXGI_COLOR_SPACE_YCBCR_STUDIO_G22_LEFT_P709;
    }
}

static bool makeProcessor(RtxState *s, QSize outSize, bool hdr, QQuickWindow *w) {
    if (s->en && s->outSize == outSize && s->outHdr == hdr)
        return true;
    s->dropOutput();
    D3D11_VIDEO_PROCESSOR_CONTENT_DESC cd = {};
    cd.InputFrameFormat = D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
    cd.InputWidth = s->inSize.width();
    cd.InputHeight = s->inSize.height();
    cd.OutputWidth = outSize.width();
    cd.OutputHeight = outSize.height();
    cd.Usage = D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;
    if (FAILED(s->vdev->CreateVideoProcessorEnumerator(&cd, &s->en)) || FAILED(s->vdev->CreateVideoProcessor(s->en.Get(), 0, &s->vp)))
        return false;
    const DXGI_FORMAT ofmt = hdr ? DXGI_FORMAT_R10G10B10A2_UNORM : DXGI_FORMAT_R8G8B8A8_UNORM;
    UINT support = 0;
    if (FAILED(s->en->CheckVideoProcessorFormat(ofmt, &support)) || !(support & D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_OUTPUT))
        return false;
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = outSize.width();
    td.Height = outSize.height();
    td.MipLevels = 1;
    td.ArraySize = 1;
    td.Format = ofmt;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    if (FAILED(s->dev->CreateTexture2D(&td, nullptr, &s->out)))
        return false;
    D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC ov = {};
    ov.ViewDimension = D3D11_VPOV_DIMENSION_TEXTURE2D;
    if (FAILED(s->vdev->CreateVideoProcessorOutputView(s->out.Get(), s->en.Get(), &ov, &s->outView)))
        return false;
    QRhi *rhi = w->rhi();
    s->rhiTex = rhi->newTexture(hdr ? QRhiTexture::RGB10A2 : QRhiTexture::RGBA8, outSize, 1);
    if (!s->rhiTex->createFrom({quint64(s->out.Get()), 0}))
        return false;
    s->sgTex = w->createTextureFromRhiTexture(s->rhiTex);
    s->outSize = outSize;
    s->outHdr = hdr;
    s->inView.Reset();
    return true;
}

QSGNode *RtxVideo::updatePaintNode(QSGNode *old, UpdatePaintNodeData *) {
    auto *node = static_cast<QSGSimpleTextureNode *>(old);
    QQuickWindow *w = window();
    if (!w || !m_router || width() < 2 || height() < 2) {
        delete node;
        return nullptr;
    }
    if (!m_s) {
        m_s = new RtxState();
        if (!init(m_s, w)) {
            m_status = m_s->status;
            QMetaObject::invokeMethod(this, "statusChanged", Qt::QueuedConnection);
            delete node;
            return nullptr;
        }
    }
    RtxState *s = m_s;
    quint64 seq = 0;
    QVideoFrame f = rv_last(reinterpret_cast<void *>(m_router), &seq);
    if (!f.isValid()) {
        delete node;
        return nullptr;
    }
    // the picture fitted into the item, in pixels: the processor scales straight to that size
    const qreal dpr = w->effectiveDevicePixelRatio();
    const QSize src(f.width() & ~1, f.height() & ~1);
    QRectF box = boundingRect();
    const qreal k = qMin(box.width() / src.width(), box.height() / src.height());
    const QSizeF fit(src.width() * k, src.height() * k);
    const QRectF dst(box.x() + (box.width() - fit.width()) / 2, box.y() + (box.height() - fit.height()) / 2, fit.width(), fit.height());
    const QSize outSize(qMax(2, int(fit.width() * dpr + 0.5)) & ~1, qMax(2, int(fit.height() * dpr + 0.5)) & ~1);
    const bool hdr = m_hdr && s->nvidia;
    const bool fresh = seq != s->lastSeq || s->outSize != outSize || s->outHdr != hdr || !s->sgTex;
    if (fresh) {
        if (!upload(s, f) || !makeProcessor(s, outSize, hdr, w)) {
            m_status = QStringLiteral("error: video processor");
            QMetaObject::invokeMethod(this, "statusChanged", Qt::QueuedConnection);
            delete node;
            return nullptr;
        }
        if (!s->inView) {
            D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC iv = {};
            iv.ViewDimension = D3D11_VPIV_DIMENSION_TEXTURE2D;
            if (FAILED(s->vdev->CreateVideoProcessorInputView(s->in.Get(), s->en.Get(), &iv, &s->inView))) {
                delete node;
                return nullptr;
            }
        }
        ID3D11VideoProcessor *vp = s->vp.Get();
        if (s->vctx1) {
            s->vctx1->VideoProcessorSetStreamColorSpace1(vp, 0, inputSpace(f.surfaceFormat()));
            s->vctx1->VideoProcessorSetOutputColorSpace1(vp, hdr ? DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
                                                                 : DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709);
        }
        s->vctx->VideoProcessorSetStreamFrameFormat(vp, 0, D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE);
        const RECT srcRect = {0, 0, src.width(), src.height()};
        const RECT dstRect = {0, 0, outSize.width(), outSize.height()};
        s->vctx->VideoProcessorSetStreamSourceRect(vp, 0, TRUE, &srcRect);
        s->vctx->VideoProcessorSetStreamDestRect(vp, 0, TRUE, &dstRect);
        s->vctx->VideoProcessorSetOutputTargetRect(vp, TRUE, &dstRect);
        QString st;
        if (s->nvidia) {
            struct { UINT version, method, enable; } vsr = {1, 2, m_vsr ? 1u : 0u};
            const HRESULT a = s->vctx->VideoProcessorSetStreamExtension(vp, 0, &NV_PPE, sizeof(vsr), &vsr);
            if (m_vsr)
                st = SUCCEEDED(a) ? QStringLiteral("vsr") : QStringLiteral("vsr refused");
            struct { UINT version, method; UINT enable : 1; UINT reserved : 31; } th = {4, 3, hdr ? 1u : 0u, 0};
            const HRESULT b = s->vctx->VideoProcessorSetStreamExtension(vp, 0, &NV_TRUEHDR, sizeof(th), &th);
            if (hdr)
                st += SUCCEEDED(b) ? QStringLiteral("+hdr") : QStringLiteral("+hdr refused");
        } else {
            st = QStringLiteral("no rtx gpu");
        }
        D3D11_VIDEO_PROCESSOR_STREAM stream = {};
        stream.Enable = TRUE;
        stream.OutputIndex = 0;
        stream.InputFrameOrField = s->frameIndex;
        stream.pInputSurface = s->inView.Get();
        if (FAILED(s->vctx->VideoProcessorBlt(vp, s->outView.Get(), s->frameIndex++, 1, &stream))) {
            m_status = QStringLiteral("error: blt");
            QMetaObject::invokeMethod(this, "statusChanged", Qt::QueuedConnection);
            delete node;
            return nullptr;
        }
        s->lastSeq = seq;
        ++m_frames;
        if (st != m_status || src != m_src || outSize != m_out) {
            m_status = st;
            m_src = src;
            m_out = outSize;
            QMetaObject::invokeMethod(this, "statusChanged", Qt::QueuedConnection);
        }
    }
    if (!node) {
        node = new QSGSimpleTextureNode();
        node->setOwnsTexture(false);
        node->setFiltering(QSGTexture::Linear);
    }
    node->setTexture(s->sgTex);
    node->setRect(dst);
    node->markDirty(QSGNode::DirtyMaterial);
    return node;
}
