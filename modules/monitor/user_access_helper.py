import os
import re
import datetime
from decimal import Decimal
import logging

try:
    from odf.opendocument import OpenDocumentText
    from odf.style import (
        Style, TextProperties, ParagraphProperties, TableProperties,
        TableColumnProperties, TableCellProperties, TableRowProperties
    )
    from odf.text import P, H, Span
    from odf.table import Table, TableColumn, TableRow, TableCell, CoveredTableCell
except ImportError:
    OpenDocumentText = None

log = logging.getLogger(__name__)

MENU_SUPER_ADMIN = [
    ("1. Manajemen User, Group & Otoritas Sistem", "View, Add, Edit, Delete, Assign Hak Akses (Full Control)"),
    ("2. Konfigurasi Parameter, Routes & Keamanan", "View, Add, Edit, Delete Parameter Sistem & Modul"),
    ("3. Master Departemen/OPD & Pemetaan Pejabat", "View, Add, Edit, Delete Master Organisasi & Pegawai"),
    ("4. Master Tarif, Produk & Rekening Anggaran", "View, Add, Edit, Delete Master Global Lintas Sektor"),
    ("5. Penetapan SKRD / STRD Seluruh Pemerintah Daerah", "View, Add, Edit, Delete, Cetak Dokumen (Full Access)"),
    ("6. Penerimaan Kas, Verifikasi STS & Reversal", "View, Add, Edit, Delete, Pembatalan Transaksi (Reversal)"),
    ("7. Rekonsiliasi Penerimaan Kas Daerah (Bank)", "View, Validasi Rekon H2H/VA/QRIS, Cetak Berita Acara"),
    ("8. Laporan Penerimaan Konsolidasi Daerah (SKPKD)", "View, Cetak Seluruh Format Laporan Konsolidasi (PDF/Excel)"),
    ("9. Monitoring Web Service & Integrasi Payment API", "View, Monitor Transaksi API, Recreate Key, Callback")
]

MENU_ADMIN_WEBR = [
    ("1. Master Tarif, Produk & Rumus Retribusi", "View, Add, Edit, Delete (Seluruh Sektor Retribusi)"),
    ("2. Pemetaan Rekening Anggaran Pendapatan OPD", "View, Add, Edit, Delete (Seluruh OPD Pengelola)"),
    ("3. Pendataan Subjek & Objek Lintas OPD", "View, Add, Edit, Delete (Seluruh OPD)"),
    ("4. Monitoring Penetapan SKRD/STRD Seluruh OPD", "View, Add, Edit, Delete, Cetak Penetapan"),
    ("5. Validasi & Rekonsiliasi DPH Lintas OPD", "View, Verifikasi, Validasi Konsolidasi Harian"),
    ("6. Laporan Penerimaan SKPKD (Konsolidasi Daerah)", "View, Cetak Laporan Penerimaan SKPKD (PDF/Excel)")
]

MENU_DLH = [
    ("1. Pendaftaran Subjek & Objek Retribusi Persampahan/Lab", "View, Add, Edit, Delete (Khusus Sektor DLH)"),
    ("2. Penetapan SKRD Retribusi Pelayanan Kebersihan", "View, Add, Edit, Delete (Status Draft), Cetak SKRD"),
    ("3. Piutang & Tagihan Retribusi DLH", "View, Add, Edit, Del (Draft), Cetak Tagihan"),
    ("4. Penerimaan Kas & Cetak Kwitansi / TBP DLH", "View, Input Kas Pembayaran, Cetak TBP"),
    ("5. Pembuatan & Pencetakan STS DLH ke Kasda", "View, Buat STS ke Kasda, Cetak Dokumen STS"),
    ("6. Daftar Penerimaan Harian (DPH) Bendahara DLH", "View, Entry Data DPH, Cetak DPH"),
    ("7. Laporan Realisasi Penerimaan Retribusi DLH", "View, Cetak Laporan Realisasi Sektoral (PDF/Excel)")
]

MENU_PASAR = [
    ("1. Pendataan Subjek & Objek Kios / Los Pasar", "View, Add, Edit, Delete (Khusus Sektor Pasar)"),
    ("2. Penetapan SKRD & STRD Pasar", "View, Add, Edit, Delete (Status Draft), Cetak SKRD"),
    ("3. Pencetakan Dokumen (SKRD / DHKR / STRD)", "View, Cetak Dokumen Penetapan & Tagihan"),
    ("4. Entry Data & Posting DPH Petugas Pasar", "View, Entry DPH, Download, Posting ke Kasda"),
    ("5. Monitoring & Laporan Piutang Retribusi Pasar", "View, Cetak Laporan Piutang Pasar"),
    ("6. Penerimaan Kas & Cetak TBP / STS Pasar", "View, Input Kas, Cetak TBP, Cetak Dokumen STS")
]

MENU_KEKAYAAN_ASET = [
    ("1. Pendataan Objek Sewa Kekayaan Daerah / Pemanfaatan Aset", "View, Add, Edit, Delete (Sektor Barang Milik Daerah)"),
    ("2. Penetapan SKRD Sewa Tanah, Gedung & Aset Daerah", "View, Add, Edit, Delete (Draft), Cetak SKRD"),
    ("3. Entry Data & Posting DPH Sewa Aset", "View, Entry DPH, Download, Posting Kasda"),
    ("4. Penerimaan Kas & Cetak TBP / STS Sewa Aset", "View, Input Kas, Cetak TBP, Cetak Dokumen STS"),
    ("5. Laporan Realisasi Pemanfaatan Kekayaan Daerah", "View, Cetak Laporan Realisasi Sewa Aset (PDF/Excel)")
]

MENU_PTSP = [
    ("1. Pendataan Subjek & Objek Perizinan / Layanan Terpadu", "View, Add, Edit, Delete (Sektor Perizinan Tertentu)"),
    ("2. Penetapan SKRD Retribusi Perizinan Tertentu", "View, Add, Edit, Delete (Draft), Cetak SKRD"),
    ("3. Penerbitan Kode Billing Virtual Account (VA) & QRIS", "View, Generate Billing VA/QRIS, Cetak Kode Bayar"),
    ("4. Verifikasi Pembayaran & Penerbitan Izin", "View, Verifikasi Status Lunas H2H Bank, Cetak TBP"),
    ("5. Pembuatan & Pencetakan STS Perizinan", "View, Buat Dokumen STS, Cetak STS"),
    ("6. Laporan Penerimaan Retribusi Perizinan", "View, Cetak Laporan Realisasi Penerimaan (PDF/Excel)")
]

MENU_WEBR_OPERATOR_STANDAR = [
    ("1. Pendaftaran Subjek & Objek Retribusi OPD", "View, Add, Edit, Delete (Khusus OPD Terdaftar)"),
    ("2. Penetapan SKRD / STRD / Invoice OPD", "View, Add, Edit, Delete (Hanya Status Draft), Cetak SKRD"),
    ("3. Piutang & Tagihan Retribusi Daerah", "View, Add, Edit, Del (Draft), Cetak Tagihan"),
    ("4. Penerimaan Kas & Cetak Kwitansi / TBP", "View, Input Kas Pembayaran, Cetak TBP"),
    ("5. Pembuatan & Pencetakan STS ke Kasda", "View, Buat Dokumen STS ke Kas Daerah, Cetak STS"),
    ("6. Daftar Penerimaan Harian (DPH) Bendahara", "View, Entry Data DPH, Cetak DPH"),
    ("7. Penerbitan Kode Billing VA & QRIS BJB", "View, Generate Billing, Cetak Kode Bayar / QRIS"),
    ("8. Laporan Realisasi Penerimaan OPD", "View, Cetak Laporan Harian/Bulanan (PDF / Excel)"),
    ("9. Master Referensi Tarif & Anggaran", "Hanya Lihat Tarif & Rekening (Read-Only)")
]

MENU_VIEW_ONLY = [
    ("1. Monitoring Penerimaan & Pembayaran Retribusi", "View / Lihat Data Pembayaran (Read-Only)"),
    ("2. Laporan Realisasi Penerimaan Retribusi", "View, Cetak Laporan Realisasi Pembayaran")
]

MATRIKS_MODUL_OPERASIONAL = [
    ("1. Pendaftaran Subjek Pajak / Retribusi", "Pendaftaran identitas wajib retribusi / badan hukum", "Ya", "Ya", "Ya", "Ya", "Ya"),
    ("2. Pendaftaran Objek Retribusi", "Pendataan objek retribusi daerah sesuai sektor unit kerja", "Ya", "Ya", "Ya", "Ya", "Ya"),
    ("3. Penetapan SKRD / STRD", "Penetapan besaran retribusi terutang & nomor ketetapan", "Ya", "Ya", "Ya", "Ya*", "Ya"),
    ("4. Pembatalan Penetapan (Reversal)", "Pembatalan nomor ketetapan yang salah sebelum bayar", "Ya", "Tidak", "Ya", "Ya", "Ya"),
    ("5. Tagihan / Piutang Retribusi", "Penerbitan surat tagihan dan monitoring piutang", "Ya", "Ya", "Ya", "Tidak", "Ya"),
    ("6. Penerimaan Kas & Tanda Bukti Pembayaran", "Pencatatan pembayaran tunai/teller dan cetak TBP", "Ya", "Ya", "Ya", "Tidak", "Ya"),
    ("7. Penerbitan Kode Billing Virtual Account (VA)", "Integrasi pembuatan nomor VA Bank BJB untuk pembayaran", "Ya", "Ya", "Tidak", "Tidak", "Ya"),
    ("8. Penerbitan Dinamis QRIS", "Penerbitan QRIS dinamis BJB untuk pembayaran digital", "Ya", "Ya", "Tidak", "Tidak", "Ya"),
    ("9. Surat Tanda Setor (STS)", "Penyusunan STS bendahara penerimaan ke Rekening Kasda", "Ya", "Ya", "Ya", "Tidak", "Ya"),
    ("10. Daftar Penerimaan Harian (DPH)", "Pencatatan rincian penerimaan harian bendahara", "Ya", "Ya", "Ya", "Tidak", "Ya"),
    ("11. Laporan Realisasi per OPD per Rekening", "Rekapitulasi penerimaan retribusi per rekening OPD", "Ya", "Tidak", "Tidak", "Tidak", "Ya"),
    ("12. Laporan Konsolidasi Penerimaan SKPKD", "Laporan konsolidasi seluruh OPD se-Kabupaten/Kota", "Ya", "Tidak", "Tidak", "Tidak", "Ya"),
    ("13. Rekonsiliasi Penerimaan Kasda", "Pencocokan data transaksi WebR dengan rekening koran", "Ya", "Tidak", "Ya", "Tidak", "Ya"),
    ("14. Master Produk & Tarif Retribusi", "Konfigurasi jenis retribusi, nominal tarif, dan dasar hukum", "Ya", "Ya", "Ya", "Ya", "Ya"),
    ("15. Master Organisasi (Departemen/OPD)", "Data referensi dinas/badan/kecamatan pengelola retribusi", "Ya", "Ya", "Ya", "Tidak", "Ya"),
    ("16. Master Rekening Anggaran", "Kodefikasi rekening pendapatan daerah (Permendagri 90/108)", "Ya", "Ya", "Ya", "Tidak", "Ya"),
    ("17. Manajemen User, Group & Hak Akses", "Pengaturan otorisasi pengguna dan pembagian peran", "Ya", "Ya", "Ya", "Ya", "Ya")
]

def determine_user_menus(uname, group, opd, peg_nama):
    uname = (uname or '').lower()
    group = (group or '').lower()
    opd = (opd or '').lower()
    if uname in ['admin', 'superadmin'] or 'superuser' in group or 'super admin' in group:
        return MENU_SUPER_ADMIN
    elif 'webr admin' in group or 'core' in group or uname in ['localme']:
        return MENU_ADMIN_WEBR
    elif 'lingkungan hidup' in opd or 'kebersihan' in opd or 'dlh' in opd:
        return MENU_DLH
    elif 'pasar' in opd or 'perdagangan' in opd:
        return MENU_PASAR
    elif 'pengelolaan keuangan' in opd or 'bpkad' in opd or 'bkad' in opd or 'aset' in opd or 'kekayaan' in opd:
        return MENU_KEKAYAAN_ASET
    elif 'ptsp' in uname or 'penanaman modal' in opd or 'dpmptsp' in opd:
        return MENU_PTSP
    elif 'view' in group:
        return MENU_VIEW_ONLY
    else:
        return MENU_WEBR_OPERATOR_STANDAR

def execute_query(conn, query_str):
    cur = conn.cursor()
    cur.execute(query_str)
    cols = [desc[0] for desc in cur.description] if cur.description else []
    results = []
    for row in cur.fetchall():
        row_dict = {}
        for idx, col in enumerate(cols):
            val = row[idx]
            if isinstance(val, (datetime.datetime, datetime.date)):
                val = val.isoformat()
            elif isinstance(val, Decimal):
                val = float(val)
            row_dict[col] = val
        results.append(row_dict)
    cur.close()
    return results

def table_exists(conn, table_name, schema='public'):
    q = f"""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = '{schema}' AND table_name = '{table_name}'
        ) as exists
    """
    try:
        res = execute_query(conn, q)
        if res and len(res) > 0:
            return bool(res[0].get('exists'))
    except Exception:
        pass
    return False

def fetch_user_access_data(conn, filter_pegawai='all'):
    """
    Menarik dan menyusun data pengguna, peran, profil pegawai, dan OPD dari database.
    Mendukung filter: 'all' (semua user) atau 'pegawai_only' (hanya user yang join ke data pegawai).
    """
    q_users = """
        SELECT id, user_name, email, status, registered_date, last_login_date 
        FROM users 
        ORDER BY id
    """
    raw_users = execute_query(conn, q_users)

    groups_map = {}
    if table_exists(conn, 'groups'):
        g_rows = execute_query(conn, "SELECT id, group_name FROM groups")
        groups_map = {g['id']: g['group_name'] for g in g_rows}

    user_groups_map = {}
    if table_exists(conn, 'users_groups'):
        ug_rows = execute_query(conn, "SELECT user_id, group_id FROM users_groups")
        for ug in ug_rows:
            uid = ug['user_id']
            gid = ug['group_id']
            gname = groups_map.get(gid, f"Group-{gid}")
            if uid not in user_groups_map:
                user_groups_map[uid] = []
            if gname not in user_groups_map[uid]:
                user_groups_map[uid].append(gname)

    dept_map = {}
    if table_exists(conn, 'departemen'):
        d_rows = execute_query(conn, "SELECT id, kode, nama FROM departemen")
        dept_map = {d['id']: d['nama'] for d in d_rows}

    user_dept_map = {}
    if table_exists(conn, 'departemen_user'):
        du_rows = execute_query(conn, "SELECT user_id, departemen_id FROM departemen_user")
        for du in du_rows:
            uid = du['user_id']
            did = du['departemen_id']
            dname = dept_map.get(did, f"OPD-{did}")
            if uid not in user_dept_map:
                user_dept_map[uid] = []
            if dname not in user_dept_map[uid]:
                user_dept_map[uid].append(dname)

    has_pegawai = table_exists(conn, 'pegawai')
    has_jabatan = table_exists(conn, 'jabatan')
    pegawai_by_id = {}
    pegawai_by_kode = {}
    pegawai_by_email = {}
    pegawai_by_user_id = {}

    if has_pegawai:
        q_peg = "SELECT id, kode, nama, nik, email FROM pegawai WHERE status = 1"
        for p in execute_query(conn, q_peg):
            pegawai_by_id[p['id']] = p
            if p.get('kode'):
                pegawai_by_kode[str(p['kode']).strip().lower()] = p
            if p.get('email'):
                pegawai_by_email[str(p['email']).strip().lower()] = p

    if table_exists(conn, 'pegawai_user'):
        q_pu = "SELECT user_id, pegawai_id FROM pegawai_user"
        for pu in execute_query(conn, q_pu):
            if pu.get('pegawai_id') in pegawai_by_id:
                pegawai_by_user_id[pu['user_id']] = pegawai_by_id[pu['pegawai_id']]

    jabatan_by_peg_id = {}
    if table_exists(conn, 'pegawai_departemen') and has_jabatan:
        q_pj = """
            SELECT pd.pegawai_id, j.nama as jabatan_nama
            FROM pegawai_departemen pd
            JOIN jabatan j ON pd.jabatan_id = j.id
        """
        for pj in execute_query(conn, q_pj):
            jabatan_by_peg_id[pj['pegawai_id']] = pj['jabatan_nama']

    processed_users = []
    no_urut = 1

    for u in raw_users:
        uid = u['id']
        uname = str(u.get('user_name', '')).strip()
        email = str(u.get('email', '')).strip()
        uname_lower = uname.lower()
        email_lower = email.lower()

        p_info = pegawai_by_user_id.get(uid) or pegawai_by_kode.get(uname_lower) or pegawai_by_email.get(email_lower)
        has_pegawai_record = bool(p_info)

        if filter_pegawai == 'pegawai_only' and not has_pegawai_record:
            continue

        peg_nama = p_info['nama'] if p_info else "-"
        peg_nik = p_info['nik'] if (p_info and p_info.get('nik')) else "-"
        peg_jabatan = jabatan_by_peg_id.get(p_info['id'], "-") if p_info else "-"

        u_groups = user_groups_map.get(uid, [])
        group_str = ", ".join(u_groups) if u_groups else "-"

        u_depts = user_dept_map.get(uid, [])
        if uname_lower in ['admin', 'superadmin', 'administrator'] or 'superuser' in group_str.lower():
            dept_str = "Seluruh Perangkat Daerah (Semua OPD / SKPKD)"
            if peg_nama == "-":
                peg_nama = "Administrator Sistem"
            if peg_jabatan == "-":
                peg_jabatan = "Super Administrator"
        elif u_depts:
            dept_str = " | ".join(u_depts[:2])
            if len(u_depts) > 2:
                dept_str += f" (+{len(u_depts)-2} OPD lainnya)"
        else:
            dept_str = "Badan Pendapatan Daerah / SKPKD"

        menus = determine_user_menus(uname_lower, group_str.lower(), dept_str.lower(), peg_nama.lower())
        status_str = "Aktif" if u.get('status') == 1 else "Non-Aktif"

        processed_users.append({
            "no": no_urut,
            "id": uid,
            "username": uname,
            "nama_pegawai": peg_nama,
            "nik": peg_nik,
            "jabatan": peg_jabatan,
            "group": group_str,
            "departemen": dept_str,
            "status": status_str,
            "has_pegawai": has_pegawai_record,
            "menus": menus
        })
        no_urut += 1

    return processed_users

def generate_odt_report(user_records, output_filepath, instansi_name="PEMERINTAH DAERAH", filter_pegawai="all"):
    """
    Menyusun dokumen OpenDocument Text (.odt) lengkap dengan styling dan matriks tabel.
    """
    if OpenDocumentText is None:
        raise RuntimeError("Library 'odfpy' belum terinstall.")

    doc = OpenDocumentText()

    instansi_style = Style(name="InstansiHeader", family="paragraph")
    instansi_style.addElement(ParagraphProperties(textalign="center", margintop="4pt", marginbottom="2pt"))
    instansi_style.addElement(TextProperties(fontname="Arial", fontsize="11pt", fontweight="bold", color="#1e3a8a"))
    doc.styles.addElement(instansi_style)

    title_style = Style(name="MainTitle", family="paragraph")
    title_style.addElement(ParagraphProperties(textalign="center", margintop="2pt", marginbottom="2pt"))
    title_style.addElement(TextProperties(fontname="Arial", fontsize="13pt", fontweight="bold", color="#0f172a"))
    doc.styles.addElement(title_style)

    subtitle_style = Style(name="SubTitle", family="paragraph")
    subtitle_style.addElement(ParagraphProperties(textalign="center", margintop="2pt", marginbottom="14pt"))
    subtitle_style.addElement(TextProperties(fontname="Arial", fontsize="9.5pt", fontstyle="italic", color="#475569"))
    doc.styles.addElement(subtitle_style)

    section_style = Style(name="SectionHeader", family="paragraph")
    section_style.addElement(ParagraphProperties(textalign="left", margintop="16pt", marginbottom="6pt"))
    section_style.addElement(TextProperties(fontname="Arial", fontsize="11pt", fontweight="bold", color="#1e40af"))
    doc.styles.addElement(section_style)

    body_text_style = Style(name="BodyTextCustom", family="paragraph")
    body_text_style.addElement(ParagraphProperties(textalign="justify", margintop="2pt", marginbottom="6pt"))
    body_text_style.addElement(TextProperties(fontname="Arial", fontsize="9pt", color="#334155"))
    doc.styles.addElement(body_text_style)

    th_cell_style = Style(name="TableHeadCell", family="table-cell")
    th_cell_style.addElement(TableCellProperties(
        backgroundcolor="#1e40af",
        paddingtop="6pt", paddingbottom="6pt", paddingleft="4pt", paddingright="4pt",
        border="0.5pt solid #0f172a"
    ))
    doc.styles.addElement(th_cell_style)

    th_text_style = Style(name="TableHeadText", family="paragraph")
    th_text_style.addElement(ParagraphProperties(textalign="center"))
    th_text_style.addElement(TextProperties(fontname="Arial", fontsize="8pt", fontweight="bold", color="#ffffff"))
    doc.styles.addElement(th_text_style)

    td_cell_even = Style(name="CellEven", family="table-cell")
    td_cell_even.addElement(TableCellProperties(
        backgroundcolor="#ffffff",
        paddingtop="3.5pt", paddingbottom="3.5pt", paddingleft="4pt", paddingright="4pt",
        border="0.5pt solid #cbd5e1"
    ))
    doc.styles.addElement(td_cell_even)

    td_cell_odd = Style(name="CellOdd", family="table-cell")
    td_cell_odd.addElement(TableCellProperties(
        backgroundcolor="#f8fafc",
        paddingtop="3.5pt", paddingbottom="3.5pt", paddingleft="4pt", paddingright="4pt",
        border="0.5pt solid #cbd5e1"
    ))
    doc.styles.addElement(td_cell_odd)

    txt_c_center = Style(name="CellTextCenter", family="paragraph")
    txt_c_center.addElement(ParagraphProperties(textalign="center"))
    txt_c_center.addElement(TextProperties(fontname="Arial", fontsize="7.5pt", color="#1e293b"))
    doc.styles.addElement(txt_c_center)

    txt_c_left = Style(name="CellTextLeft", family="paragraph")
    txt_c_left.addElement(ParagraphProperties(textalign="left"))
    txt_c_left.addElement(TextProperties(fontname="Arial", fontsize="7.5pt", color="#1e293b"))
    doc.styles.addElement(txt_c_left)

    filter_label = "Semua User Terdaftar" if filter_pegawai == "all" else "Hanya User Terhubung Pegawai"
    doc.text.addElement(P(stylename=instansi_style, text=instansi_name.upper()))
    doc.text.addElement(P(stylename=title_style, text="DAFTAR LIST USER & MATRIKS HAK AKSES SISTEM"))
    doc.text.addElement(P(stylename=subtitle_style, text=f"Aplikasi OpenSIPKD Web Retribusi Daerah (WebR) • Filter: {filter_label} • Dicetak pada {datetime.date.today().strftime('%d %B %Y')}"))

    doc.text.addElement(P(stylename=section_style, text="I. MATRIKS PEMETAAN PENGGUNA, JABATAN & HAK AKSES"))
    doc.text.addElement(P(stylename=body_text_style, text=(
        "Tabel berikut merinci seluruh akun pengguna yang terdaftar di dalam sistem basis data, "
        "beserta pemetaan identitas pegawai, jabatan, unit kerja pengelola, dan peran (role) yang berlaku."
    )))

    tbl_users = Table(name="TabelUserAkses")
    col_widths = ["6%", "6%", "12%", "18%", "18%", "20%", "12%", "8%"]
    for i, w in enumerate(col_widths):
        c_style = Style(name=f"ColW_{i}", family="table-column")
        c_style.addElement(TableColumnProperties(columnwidth=w))
        doc.styles.addElement(c_style)
        tbl_users.addElement(TableColumn(stylename=c_style))

    headers = ["No", "ID", "Username", "Nama Pegawai & NIK", "Jabatan / Kedudukan", "OPD / Unit Kerja", "Peran (Role)", "Status"]
    header_row = TableRow()
    for h_txt in headers:
        cell = TableCell(stylename=th_cell_style)
        cell.addElement(P(stylename=th_text_style, text=h_txt))
        header_row.addElement(cell)
    tbl_users.addElement(header_row)

    for idx, u in enumerate(user_records):
        row_style = td_cell_even if idx % 2 == 0 else td_cell_odd
        r = TableRow()

        r_data = [
            (str(u["no"]), txt_c_center),
            (str(u["id"]), txt_c_center),
            (u["username"], txt_c_left),
            (f"{u['nama_pegawai']}\nNIK: {u['nik']}", txt_c_left),
            (u["jabatan"], txt_c_left),
            (u["departemen"], txt_c_left),
            (u["group"], txt_c_left),
            (u["status"], txt_c_center)
        ]

        for val, p_style in r_data:
            cell = TableCell(stylename=row_style)
            for line in val.split("\n"):
                cell.addElement(P(stylename=p_style, text=line))
            r.addElement(cell)

        tbl_users.addElement(r)

    doc.text.addElement(tbl_users)

    doc.text.addElement(P(stylename=section_style, text="II. MATRIKS RINCIAN MODUL OPERASIONAL SISTEM"))
    tbl_modul = Table(name="TabelModulOperasional")
    m_widths = ["25%", "35%", "8%", "8%", "8%", "8%", "8%"]
    for i, w in enumerate(m_widths):
        c_style = Style(name=f"ColM_{i}", family="table-column")
        c_style.addElement(TableColumnProperties(columnwidth=w))
        doc.styles.addElement(c_style)
        tbl_modul.addElement(TableColumn(stylename=c_style))

    m_headers = ["Modul / Sub-Sistem", "Deskripsi Fungsi", "Super Admin", "Admin WebR", "DLH / Teknis", "Bendahara", "Operator OPD"]
    m_hrow = TableRow()
    for mh in m_headers:
        cell = TableCell(stylename=th_cell_style)
        cell.addElement(P(stylename=th_text_style, text=mh))
        m_hrow.addElement(cell)
    tbl_modul.addElement(m_hrow)

    for idx, row in enumerate(MATRIKS_MODUL_OPERASIONAL):
        r_style = td_cell_even if idx % 2 == 0 else td_cell_odd
        tr = TableRow()
        for c_idx, val in enumerate(row):
            p_style = txt_c_left if c_idx < 2 else txt_c_center
            c = TableCell(stylename=r_style)
            c.addElement(P(stylename=p_style, text=val))
            tr.addElement(c)
        tbl_modul.addElement(tr)

    doc.text.addElement(tbl_modul)

    doc.text.addElement(P(stylename=section_style, text="III. PENJELASAN HAK AKSES PENGGUNA TERDAFTAR"))
    for u in user_records:
        u_box = P(stylename=body_text_style, text=f"• [{u['username']}] {u['nama_pegawai']} - Peran: {u['group']} ({u['departemen']}):")
        doc.text.addElement(u_box)
        for m_name, m_perm in u["menus"]:
            m_item = P(stylename=body_text_style, text=f"    - {m_name}: {m_perm}")
            doc.text.addElement(m_item)

    doc.save(output_filepath)
    log.info(f"ODT report successfully generated: {output_filepath}")
    return output_filepath
