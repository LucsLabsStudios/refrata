// RtxVideo: the video picture through the GPU's own video processor, with NVIDIA RTX Video
// (Super Resolution: AI upscaling; TrueHDR: SDR video shown as HDR10). See rtx.cpp.
#pragma once
#include <QtQuick/QQuickItem>
#include <QtMultimedia/QVideoFrame>

struct RtxState;

class RtxVideo : public QQuickItem {
    Q_OBJECT
    Q_PROPERTY(qulonglong router READ router WRITE setRouter NOTIFY routerChanged)
    Q_PROPERTY(bool superResolution READ superResolution WRITE setSuperResolution NOTIFY settingsChanged)
    Q_PROPERTY(bool trueHdr READ trueHdr WRITE setTrueHdr NOTIFY settingsChanged)
    Q_PROPERTY(QString status READ status NOTIFY statusChanged)
    Q_PROPERTY(QSize sourceSize READ sourceSize NOTIFY statusChanged)
    Q_PROPERTY(QSize outputSize READ outputSize NOTIFY statusChanged)
    Q_PROPERTY(int frames READ frames NOTIFY statusChanged)
public:
    explicit RtxVideo(QQuickItem *parent = nullptr);
    ~RtxVideo() override;
    qulonglong router() const { return m_router; }
    void setRouter(qulonglong r);
    bool superResolution() const { return m_vsr; }
    void setSuperResolution(bool on);
    bool trueHdr() const { return m_hdr; }
    void setTrueHdr(bool on);
    QString status() const;
    QSize sourceSize() const;
    QSize outputSize() const;
    int frames() const;
    // the router (vout.cpp) has a new picture
    void pictureArrived() { update(); }
Q_SIGNALS:
    void routerChanged();
    void settingsChanged();
    void statusChanged();
protected:
    QSGNode *updatePaintNode(QSGNode *old, UpdatePaintNodeData *) override;
    void releaseResources() override;
private:
    qulonglong m_router = 0;
    bool m_vsr = true;
    bool m_hdr = false;
    RtxState *m_s = nullptr;   // render thread only
    mutable QString m_status;
    QSize m_src, m_out;
    int m_frames = 0;
};
