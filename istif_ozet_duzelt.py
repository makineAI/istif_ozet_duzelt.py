import os
import json
import re
import time
import random
import sys
import requests

# ==========================================
# ⚙️ AYARLAR (0 SCRAPE KREDİSİ - SADECE BASEROW)
# ==========================================
BASEROW_TOKEN = os.environ.get("BASEROW_TOKEN")
BASEROW_TABLE_ID = "1197625"  # İstif Makineleri Tablosu

# 🎯 MOD SEÇİMİ:
# True  -> Sadece en baştaki ilk HEDEF_SATIR_SAYISI (500) kaydın Urun_Ozeti alanını yeniler.
# False -> Tüm tabloyu (19.429 kayıt) tarar, SADECE İngilizce / boş / kısa kalmış özetleri düzeltir.
SADECE_ILK_N_SATIR = True
HEDEF_SATIR_SAYISI = 500

# 🧪 ÖNİZLEME MODU:
# True  -> Baserow'a yazmaz, sadece örnek 5 makinenin Eski / Yeni özetini ekrana basar.
# False -> Baserow'u toplu (batch) olarak günceller.
DRY_RUN = False

BATCH_BOYUTU = 50

if not BASEROW_TOKEN:
    print("❌ HATA: BASEROW_TOKEN ortam değişkeni bulunamadı!")
    sys.exit(1)

HEADERS_BASEROW = {
    "Authorization": f"Token {BASEROW_TOKEN}",
    "Content-Type": "application/json",
}

GECERSIZ_DEGERLER = {"", "n/a", "na", "n.a.", "k.a.", "-", "--", "none", "null", "unknown", "bilinmiyor"}

# ==========================================
# 🛡️ BASEROW 429 / 5XX / 520 KORUMALI İSTEK
# ==========================================
def baserow_istek_at(method, url, deneme_sayisi=4, **kwargs):
    kwargs.setdefault("headers", HEADERS_BASEROW)
    kwargs.setdefault("timeout", 30)
    son_res = None

    for deneme in range(1, deneme_sayisi + 1):
        try:
            res = requests.request(method, url, **kwargs)
            son_res = res
            if res.status_code not in (429, 500, 502, 503, 504, 520, 522, 524):
                return res
            bekleme = deneme * 3
            print(f"   ⏳ Baserow yoğun ({res.status_code}), {bekleme}s sonra tekrar deneniyor ({deneme}/{deneme_sayisi})...")
            time.sleep(bekleme)
        except Exception as e:
            if deneme == deneme_sayisi:
                print(f"   ⚠️ Baserow bağlantı hatası: {e}")
                return son_res
            time.sleep(deneme * 2)

    return son_res

def baserow_metin_coz(deger):
    if not deger:
        return ""
    if isinstance(deger, list):
        return str(deger[0].get("value", "") if isinstance(deger[0], dict) else deger[0]).strip()
    if isinstance(deger, dict):
        return str(deger.get("value", "")).strip()
    return str(deger).strip()

def gecerli_mi(val):
    if val is None:
        return False
    return str(val).strip().lower() not in GECERSIZ_DEGERLER

def havuzdan_al(havuz, *anahtarlar):
    if not isinstance(havuz, dict):
        return None
    kucuk_harita = {str(k).strip().lower(): v for k, v in havuz.items()}
    for anahtar in anahtarlar:
        v = kucuk_harita.get(anahtar.strip().lower())
        if gecerli_mi(v):
            return str(v).strip()
    return None

def ozet_zayif_veya_ingilizce_mi(ozet):
    """Tüm tablo taraması seçilirse İngilizce, boş veya zayıf özetleri yakalar."""
    if not ozet or len(str(ozet).strip()) < 160:
        return True
    metin = str(ozet).lower()
    ingilizce_ipucu = [
        " the ", " with ", " forklift", " lifting capacity", " load capacity",
        " operating weight", " manufactured by", " engine power", " travel speed",
        " turning radius", " find detailed", " specs ", " datasheet"
    ]
    eslesme = sum(1 for kelime in ingilizce_ipucu if kelime in metin)
    return eslesme >= 2

# ==========================================
# 🧠 KATEGORİ BAZLI B2B + SEO ÖZET MOTORU (İSTİF MAKİNELERİ)
# ==========================================
def kategori_baglami_belirle(alt_kategori, yakit):
    kat = (alt_kategori or "").lower()
    y = (yakit or "Dizel").lower()

    if "reach truck" in kat or "riç" in kat:
        return (
            "yüksek raflı depolar, dar koridorlu lojistik merkezleri ve seri palet istifleme operasyonları",
            "reach truck (dar koridor yüksek raf istif makinesi)",
        )
    if "transpalet" in kat:
        return (
            "depo içi yatay palet transferi, tır yükleme-boşaltma rampaları ve perakende dağıtım merkezleri",
            "akülü transpalet (palet taşıma makinesi)",
        )
    if "istif" in kat and "konteyner" not in kat:
        return (
            "orta ve dar koridorlu depolar, üretim hatları ve katlı raf istifleme çalışmaları",
            "akülü istif makinesi (stacker)",
        )
    if "sipariş" in kat or "siparis" in kat or "order" in kat:
        return (
            "e-ticaret depoları, yedek parça merkezleri ve çok katlı raf sipariş toplama operasyonları",
            "sipariş toplayıcı (order picker)",
        )
    if "dar koridor" in kat or "vna" in kat or "taret" in kat:
        return (
            "yüksek yoğunluklu çok dar koridorlu (VNA) depolar ve tavan yüksekliğinden maksimum yararlanan lojistik tesisleri",
            "dar koridor (VNA) forklift / taretli istif makinesi",
        )
    if "çok yönlü" in kat or "cok yonlu" in kat or "4 yönlü" in kat:
        return (
            "profil, boru, kereste ve uzun yüklerin dar koridorlarda güvenli taşınması",
            "çok yönlü (4 yönlü) forklift",
        )
    if "yandan" in kat or "side loader" in kat:
        return (
            "çelik servis merkezleri, kereste-panel tesisleri ve uzun malzeme stok sahaları",
            "yandan yüklemeli (side loader) forklift",
        )
    if "araç arkası" in kat or "araç üstü" in kat or "kamyon" in kat:
        return (
            "yapı malzemesi dağıtımı, saha teslimatları ve kamyon arkası bağımsız yükleme-boşaltma işleri",
            "araç arkası (kamyon arkası) taşınabilir forklift",
        )
    if "konteyner" in kat or "reach stacker" in kat:
        return (
            "liman terminalleri, demiryolu intermodal sahaları ve yüksek tonajlı dolu/boş konteyner istifleme operasyonları",
            "konteyner istifleyici (reach stacker)",
        )
    if "teleskopik" in kat or "telehandler" in kat:
        return (
            "inşaat şantiyeleri, tarım-sanayi yükleme sahaları ve yüksek erişimli malzeme taşıma işleri",
            "teleskopik yükleyici (telehandler)",
        )
    if "çekici" in kat or "cekici" in kat or "tow" in kat:
        return (
            "fabrika içi süt koşusu (milk-run) hat besleme, havalimanı ve lojistik römork çekme işleri",
            "elektrikli çekici (tow tractor)",
        )
    if "arazi" in kat or "rough" in kat:
        return (
            "bozuk şantiye zeminleri, maden-mermer sahaları ve açık alan ağır yük taşıma operasyonları",
            "arazi tipi forklift",
        )
    if "ex-proof" in kat or "atex" in kat:
        return (
            "kimya, boya, petrokimya ve patlayıcı atmosfer riski bulunan ATEX belgeli sanayi tesisleri",
            "Ex-Proof (ATEX) korumalı forklift",
        )

    # Standart Denge Ağırlıklı Forkliftler (Dizel / Elektrikli / LPG / Akülü)
    if "elektrik" in y or "akülü" in kat:
        return (
            "kapalı depolar, gıda-ilaç üretim tesisleri, lojistik merkezleri ve sıfır emisyon gerektiren iç mekân operasyonları",
            "akülü elektrikli forklift",
        )
    if "lpg" in y:
        return (
            "hem kapalı depo hem açık saha geçişli lojistik merkezleri, üretim tesisleri ve hızlı yakıt ikmali gerektiren vardiyalı işler",
            "LPG'li denge ağırlıklı forklift",
        )
    return (
        "açık saha lojistiği, sanayi tesisleri, liman-depo yükleme alanları ve ağır paletli malzeme elleçleme operasyonları",
        f"{y} denge ağırlıklı forklift",
    )

def istif_b2b_ozet_uret(row):
    marka = baserow_metin_coz(row.get("Marka"))
    model = baserow_metin_coz(row.get("Model"))
    alt_kat = baserow_metin_coz(row.get("Alt_Kategori"))
    yakit = baserow_metin_coz(row.get("Yakit_Tipi")) or "Dizel"
    kapasite = row.get("Kapasite_Ton")
    agirlik = row.get("Makine_Agirligi_Ton")
    kaldirma = row.get("Kaldirma_Yuksekligi_m")
    donus = row.get("Donus_Yaricapi_m")
    motor_uretici = baserow_metin_coz(row.get("Motor_Ureticisi"))
    motor_gucu = row.get("Motor_Gucu_kW")

    havuz_ham = row.get("Teknik_Detay_Havuzu")
    havuz = {}
    if isinstance(havuz_ham, dict):
        havuz = havuz_ham
    elif isinstance(havuz_ham, str) and havuz_ham.strip():
        try:
            havuz = json.loads(havuz_ham)
        except Exception:
            havuz = {}

    yuk_merkezi = havuzdan_al(havuz, "Yük Merkezi", "Centre of gravity", "Center of gravity", "Load center", "Load centre")
    koridor = havuzdan_al(
        havuz,
        "Koridor Genişliği (1000x1200 Palet Enine - Ast)",
        "Koridor Genişliği (800x1200 Palet Boyuna - Ast)",
        "Çalışma Koridoru Genişliği (Ast)",
        "Minimum Çalışma Koridoru Genişliği (Ast)",
        "Working aisle width",
        "Aisle width",
    )
    seyir_hizi = havuzdan_al(havuz, "Seyir Hızı (Yüklü / Yüksüz)", "Seyir Hızı", "Maksimum Seyir Hızı", "Travel speed with / without load", "Travel speed")
    sanziman = havuzdan_al(havuz, "Sürüş Kontrol / Şanzıman Tipi", "Şanzıman Tipi", "Sürüş / Kontrol Sistemi", "Transmission type", "Transmission")
    lastik = havuzdan_al(havuz, "Standart Tekerlek / Lastikler", "Tekerlek / Lastikler", "Standard tyres", "Standard tires", "Tyres")
    voltaj = havuzdan_al(havuz, "Akü Voltajı", "Nominal Akü Voltajı", "Sistem Voltajı", "Voltage", "Battery voltage")
    aku_kap = havuzdan_al(havuz, "Akü Kapasitesi (Ah)", "Batarya Enerji Kapasitesi (kWh)", "Battery capacity", "Battery energy")
    motor_tipi = havuzdan_al(havuz, "Motor Tipi / Modeli", "Motor Modeli", "Engine type", "Engine model")
    emisyon = havuzdan_al(havuz, "Emisyon Standardı", "Egzoz Emisyon Standardı", "Emissions level", "Emission level")
    genislik = havuzdan_al(havuz, "Toplam Genişlik (b1)", "Toplam Genişlik", "Taşıma Genişliği", "Total width", "Overall width")
    kapali_direk = havuzdan_al(havuz, "Kapalı Direk Yüksekliği (h1)", "Construction height", "Collapsed height")
    seri = havuzdan_al(havuz, "Model Serisi", "Model series")

    kullanim_alani, makine_tanimi = kategori_baglami_belirle(alt_kat, yakit)
    makine_tam_ad = f"{marka} {model}".strip()
    seri_ek = f" ({seri} serisi)" if (seri and seri.lower() != model.lower()) else ""
    kap_sinif = f"{kapasite} ton kaldırma kapasitesi sınıfında yer alan " if gecerli_mi(kapasite) else ""

    # 1. Giriş Cümlesi (Marka + Model + Kullanım Alanı + SEO Terimi)
    giris_secenekleri = [
        f"{marka} mühendisliğiyle {kullanim_alani} için geliştirilen {makine_tam_ad}{seri_ek}, {kap_sinif}yüksek çalışma verimliliği ve güvenilir yük dengesi sunan profesyonel bir {makine_tanimi} modelidir.".strip(),
        f"{kullanim_alani} kapsamında seri yükleme-boşaltma ve düşük işletme maliyeti hedefleyen {makine_tam_ad}{seri_ek}, {kap_sinif}{marka} ürün gamının öne çıkan {makine_tanimi} çözümlerindendir.",
        f"Yoğun vardiya temposunda kesintisiz malzeme elleçleme sağlayan {makine_tam_ad}{seri_ek}, {kap_sinif}{kullanim_alani} için tasarlanmış dayanıklı bir {makine_tanimi} seçeneğidir.",
    ]
    bolum_1 = random.choice(giris_secenekleri)
    if bolum_1:
        ilk = "İ" if bolum_1[0] == "i" else bolum_1[0].upper()
        bolum_1 = ilk + bolum_1[1:]

    # 2. Kapasite, Kaldırma Yüksekliği ve Ağırlık
    kap_parcalari = []
    if gecerli_mi(kapasite):
        ym_ek = f"{yuk_merkezi} yük merkezinde " if yuk_merkezi else ""
        kap_parcalari.append(f"{ym_ek}{kapasite} ton kaldırma kapasitesi")
    if gecerli_mi(kaldirma):
        kap_parcalari.append(f"{kaldirma} metre kaldırma yüksekliği")
    if gecerli_mi(agirlik):
        kap_parcalari.append(f"{agirlik} ton makine ağırlığı")

    bolum_2 = ""
    if kap_parcalari:
        kap_metni = ", ".join(kap_parcalari[:-1]) + (" ve " + kap_parcalari[-1] if len(kap_parcalari) > 1 else kap_parcalari[0])
        bolum_2 = f"Yükleme ve istifleme performansında {kap_metni} değerleriyle çalışan {makine_tam_ad}, paletli ve ağır yüklerin güvenli biçimde taşınmasını sağlar."

    # 3. Motor / Batarya ve Sürüş Altyapısı
    if "elektrik" in yakit.lower():
        el_parcalar = []
        if voltaj:
            el_parcalar.append(f"{voltaj}{f' ({aku_kap})' if aku_kap else ''} batarya sistemi")
        if gecerli_mi(motor_gucu):
            el_parcalar.append(f"{motor_gucu} kW elektrik motoru")
        el_metin = ", ".join(el_parcalar) if el_parcalar else (f"{motor_uretici} tahrik sistemi" if gecerli_mi(motor_uretici) else "yüksek verimli elektrikli tahrik sistemi")
        hiz_ek = f", {seyir_hizi} seyir hızıyla" if seyir_hizi else ""
        bolum_3 = f"Sıfır egzoz emisyonlu {el_metin} ile donatılan model{hiz_ek} kapalı depo ve üretim sahalarında sessiz, ekonomik ve kesintisiz çalışma imkânı sunar."
    else:
        motor_detay = f"{motor_uretici} {motor_tipi or ''}".strip() if gecerli_mi(motor_uretici) else f"yüksek torklu {yakit.lower()}"
        guc_str = f"{motor_gucu} kW güç üreten " if gecerli_mi(motor_gucu) else ""
        em_str = f" ({emisyon} emisyon sınıfında)" if emisyon else ""
        sanz_str = f" ve {sanziman} şanzıman yapısıyla" if sanziman else ""
        hiz_ek = f", {seyir_hizi} seyir hızıyla" if seyir_hizi else ""
        bolum_3 = f"Gücünü {guc_str}{motor_detay}{em_str} motordan alan {makine_tam_ad}{sanz_str}{hiz_ek} rampalı ve yoğun çalışma sahalarında kararlı çekiş ve yakıt verimliliği sağlar."

    # 4. Manevra, Koridor ve Boyutlar
    manevra_parcalari = []
    if gecerli_mi(donus):
        manevra_parcalari.append(f"{donus} metre dönüş yarıçapı")
    if koridor:
        manevra_parcalari.append(f"{koridor} çalışma koridoru genişliği")
    if genislik:
        manevra_parcalari.append(f"{genislik} toplam genişlik")
    if kapali_direk:
        manevra_parcalari.append(f"{kapali_direk} kapalı direk yüksekliği")

    bolum_4 = ""
    if manevra_parcalari:
        man_str = ", ".join(manevra_parcalari[:-1]) + (" ve " + manevra_parcalari[-1] if len(manevra_parcalari) > 1 else manevra_parcalari[0])
        lastik_ek = f" {lastik} lastik/tekerlek donanımı ise zemin tutuşunu ve sürüş konforunu destekler." if lastik else ""
        bolum_4 = f"Kompakt şasi geometrisinde {man_str} ölçüleriyle öne çıkan makine, dar raf aralarında ve yükleme rampalarında yüksek manevra kabiliyeti sunar.{lastik_ek}"

    return " ".join([b for b in [bolum_1, bolum_2, bolum_3, bolum_4] if b]).strip()

# ==========================================
# 🚀 ANA GÜNCELLEME MOTORU (BATCH PATCH)
# ==========================================
def main():
    print(f"🚀 İstif Makineleri Urun_Ozeti Güncelleme Motoru Başlıyor (Tablo: {BASEROW_TABLE_ID})")
    print(f"⚙️ Mod: {'İlk ' + str(HEDEF_SATIR_SAYISI) + ' satır' if SADECE_ILK_N_SATIR else 'Tüm tablo (sadece zayıf/EN özetler)'} | DRY_RUN: {DRY_RUN}\n")

    guncellenecekler = []
    sayfa = 1
    okunan_toplam = 0

    while True:
        url = (
            f"https://api.baserow.io/api/database/rows/table/{BASEROW_TABLE_ID}/"
            f"?user_field_names=true&order_by=id&size=200&page={sayfa}"
        )
        res = baserow_istek_at("GET", url)
        if res is None or res.status_code != 200:
            kod = res.status_code if res is not None else "Bağlantı Yok"
            print(f"❌ Sayfa {sayfa} okunamadı ({kod}). İşlem durduruldu.")
            break

        veri = res.json()
        satirlar = veri.get("results", [])
        if not satirlar:
            break

        for row in satirlar:
            okunan_toplam += 1
            if SADECE_ILK_N_SATIR and okunan_toplam > HEDEF_SATIR_SAYISI:
                break

            mevcut_ozet = baserow_metin_coz(row.get("Urun_Ozeti"))
            if not SADECE_ILK_N_SATIR and not ozet_zayif_veya_ingilizce_mi(mevcut_ozet):
                continue

            yeni_ozet = istif_b2b_ozet_uret(row)
            guncellenecekler.append({
                "id": row["id"],
                "marka_model": f"{baserow_metin_coz(row.get('Marka'))} {baserow_metin_coz(row.get('Model'))}".strip(),
                "eski": mevcut_ozet,
                "yeni": yeni_ozet,
            })

        print(f"📄 Sayfa {sayfa} okundu | Toplam okunan: {min(okunan_toplam, HEDEF_SATIR_SAYISI) if SADECE_ILK_N_SATIR else okunan_toplam} | Güncellenecek aday: {len(guncellenecekler)}")

        if SADECE_ILK_N_SATIR and okunan_toplam >= HEDEF_SATIR_SAYISI:
            break
        if not veri.get("next"):
            break
        sayfa += 1

    print(f"\n🎯 Toplam {len(guncellenecekler)} makinenin Urun_Ozeti güncellenmek üzere hazırlandı.\n")

    if not guncellenecekler:
        print("✅ Güncellenecek kayıt bulunamadı!")
        return

    # İlk 3 örneği kontrol amaçlı ekrana bas
    print("🔎 İLK 3 ÖRNEK ÖNİZLEME:")
    for ornek in guncellenecekler[:3]:
        print("-" * 60)
        print(f"📌 ID={ornek['id']} | {ornek['marka_model']}")
        print(f"   ESKİ: {ornek['eski'][:180]}...")
        print(f"   YENİ: {ornek['yeni']}")
    print("-" * 60 + "\n")

    if DRY_RUN:
        print("🧪 DRY_RUN=True olduğu için Baserow'a yazılmadı. Beğendiysen DRY_RUN=False yapıp çalıştır!")
        return

    batch_url = f"https://api.baserow.io/api/database/rows/table/{BASEROW_TABLE_ID}/batch/?user_field_names=true"
    guncellenen = 0

    for i in range(0, len(guncellenecekler), BATCH_BOYUTU):
        paket = guncellenecekler[i : i + BATCH_BOYUTU]
        payload = {"items": [{"id": x["id"], "Urun_Ozeti": x["yeni"]} for x in paket]}

        res = baserow_istek_at("PATCH", batch_url, json=payload)
        if res is not None and res.status_code == 200:
            guncellenen += len(paket)
            print(f"✅ Paket güncellendi: {guncellenen}/{len(guncellenecekler)} (Son ID: {paket[-1]['id']})")
        else:
            kod = res.status_code if res is not None else "Bağlantı Yok"
            detay = res.text[:250] if res is not None else ""
            print(f"❌ Paket hatası ({kod}) [ID {paket[0]['id']} - {paket[-1]['id']}]: {detay}")
        time.sleep(0.4)

    print(f"\n🎉 TAMAMLANDI! Toplam {guncellenen} istif makinesinin Urun_Ozeti alanı 0 krediyle yenilendi.")

if __name__ == "__main__":
    main()
