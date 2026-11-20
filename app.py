import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st
import pandas as pd
from fin_guard.db import init_db, connect, log_action, load_extraction_logs, load_threshold_alerts
from fin_guard.data_service import (
    load_transactions, load_anomalies, save_transactions,
    sync_perfect_vision
)
from fin_guard.demo import generate_demo
from fin_guard.train import train_model
from fin_guard.detect import run_detection
from fin_guard.explain import explain_transaction
from fin_guard.investigations import open_investigation, add_note, label_transaction
from fin_guard.reports import export_csv, export_excel, export_pdf

st.set_page_config(page_title="FIN-GUARD AI", page_icon="🛡️", layout="wide")
init_db()
st.title("🛡️ FIN-GUARD AI")
st.caption("Détection statistique d'opérations atypiques — VAE + moteur d'investigation")
pages = ["Vue d'ensemble", "Transactions", "Synchronisation PERFECT VISION",
         "Alertes seuil 15 millions", "Détection & alertes", "Entraînement VAE",
         "Investigations", "Assistant d'analyse", "Rapports"]
page = st.sidebar.radio("Navigation", pages)

if page == "Vue d'ensemble":
    tx = load_transactions()
    an = load_anomalies(10000)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Transactions", f"{len(tx):,}")
    c2.metric("Anomalies classées", f"{len(an):,}")
    c3.metric("Risque élevé", int((an.risk_level=="HIGH").sum()) if not an.empty else 0)
    c4.metric("Montant total", f"{tx.amount.sum():,.0f} XOF" if not tx.empty else "0 XOF")
    threshold_alerts = load_threshold_alerts(10000)
    st.metric("Alertes métier de seuil (15 M XOF)", f"{len(threshold_alerts):,}")
    if not an.empty:
        st.subheader("Répartition des niveaux de risque")
        st.bar_chart(an.risk_level.value_counts())
        st.subheader("Transactions les mieux classées")
        st.dataframe(an.head(30), use_container_width=True)

elif page == "Transactions":
    st.subheader("Importer des transactions")
    upload = st.file_uploader("Importer CSV", type=["csv"])
    if upload:
        df = pd.read_csv(upload)
        st.dataframe(df.head())
        if st.button("Enregistrer dans SQLite"):
            n = save_transactions(df, "csv")
            log_action("USER","IMPORT_CSV","transactions",details=f"{n} lignes")
            st.success(f"{n} transactions importées.")
    st.subheader("Base locale")
    st.dataframe(load_transactions().tail(500), use_container_width=True)

elif page == "Synchronisation PERFECT VISION":
    st.subheader("Synchronisation de la base PERFECT VISION")
    st.caption("Extraction en lecture seule des dépôts (DEPO) et retraits (RETR), "
               "sur les comptes dont le préfixe est 2512 et REF_CPTE E/A.")
    from datetime import date, datetime, timedelta
    filter_period = st.checkbox("Limiter la synchronisation à une période")
    start_date = end_date = None
    if filter_period:
        col1, col2 = st.columns(2)
        start_date = col1.date_input(
            "Date de début (incluse)", value=date.today() - timedelta(days=30)
        )
        end_date = col2.date_input("Date de fin (incluse)", value=date.today())
        if start_date > end_date:
            st.error("La date de début doit être antérieure ou égale à la date de fin.")
    limit = st.number_input("Limite maximale", min_value=1, max_value=1000000,
                            value=10000, step=1000)
    if st.button("Lancer la synchronisation", type="primary"):
        try:
            start_value = datetime.combine(start_date, datetime.min.time()) if start_date else None
            # La date de fin choisie par l'utilisateur est incluse dans l'interface;
            # le SQL utilise une borne de fin exclusive.
            end_value = datetime.combine(end_date + timedelta(days=1), datetime.min.time()) if end_date else None
            if filter_period and start_date > end_date:
                raise ValueError("Période invalide : la date de début dépasse la date de fin.")
            with st.spinner("Connexion et synchronisation en cours..."):
                result = sync_perfect_vision(
                    start_date=start_value, end_date=end_value,
                    limit=int(limit), account_prefix="2512"
                )
            st.success(f"Synchronisation terminée : {result['statut']}")
            m1, m2, m3 = st.columns(3)
            m1.metric("Lignes source", f"{result['nb_lignes_source']:,}")
            m2.metric("Lignes importées", f"{result['nb_lignes_importees']:,}")
            m3.metric("Doublons détectés", f"{result['nb_doublons_detectes']:,}")
            st.metric("Lignes rejetées", f"{result['nb_lignes_rejetees']:,}")
            st.subheader("Résultat des règles de seuil")
            a1, a2, a3 = st.columns(3)
            a1.metric("Opérations ≥ seuil individuellement",
                      f"{result['nb_alertes_montant']:,}")
            a2.metric("Adhérents dont le cumul ≥ seuil",
                      f"{result['nb_adherents_seuil_cumule']:,}")
            a3.metric("Opérations concernées",
                      f"{result['nb_operations_a_alerter']:,}")
            if result.get("details"):
                st.info(result["details"])
            if result["nb_lignes_extraites"] < result["nb_lignes_source"]:
                st.warning(
                    "La limite d'extraction a été atteinte : les cumuls par adhérent "
                    "sont calculés sur toute la période, mais les alertes individuelles "
                    "ne portent que sur les opérations effectivement extraites. "
                    "Augmentez la limite pour couvrir toutes les opérations."
                )
        except Exception as e:
            st.error(f"Échec de la synchronisation : {e}")

    st.subheader("Journal des extractions")
    logs = load_extraction_logs(200)
    if logs.empty:
        st.info("Aucune synchronisation enregistrée pour le moment.")
    else:
        st.dataframe(logs, use_container_width=True, hide_index=True)
        st.download_button(
            "Exporter le journal CSV",
            logs.to_csv(index=False).encode("utf-8-sig"),
            file_name="journal_extractions_fin_guard_ai.csv",
            mime="text/csv"
        )

elif page == "Alertes seuil 15 millions":
    st.subheader("Surveillance des opérations à montant élevé")
    st.caption(
        "Une alerte est générée si une opération atteint individuellement "
        "15 000 000 XOF ou si le cumul des opérations d'un adhérent sur "
        "la période d'extraction atteint 15 000 000 XOF."
    )
    alerts = load_threshold_alerts(10000)
    if alerts.empty:
        st.info("Aucune alerte de seuil enregistrée. Lancez une synchronisation PERFECT VISION.")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Alertes enregistrées", f"{len(alerts):,}")
        col2.metric("Alertes montant individuel",
                    int((alerts.alert_type == "MONTANT_INDIVIDUEL").sum()))
        col3.metric("Alertes cumul adhérent",
                    int((alerts.alert_type == "CUMUL_ADHERENT").sum()))
        st.dataframe(alerts, use_container_width=True, hide_index=True)
        st.download_button(
            "Exporter les alertes CSV",
            alerts.to_csv(index=False).encode("utf-8-sig"),
            file_name="alertes_seuil_15_millions.csv",
            mime="text/csv"
        )

elif page == "Détection & alertes":
    st.subheader("Lancer l'analyse")
    if st.button("Exécuter la détection VAE"):
        with st.spinner("Analyse en cours..."):
            try:
                n = run_detection()
                st.success(f"{n} transactions analysées.")
            except Exception as e:
                st.error(str(e))
    st.dataframe(load_anomalies(500), use_container_width=True)

elif page == "Entraînement VAE":
    st.info("Le modèle est entraîné sur l'historique disponible. Pour un usage réel, constituez un jeu d'apprentissage représentatif et contrôlé.")
    epochs = st.slider("Nombre maximal d'époques", 5, 100, 35)
    if st.button("Entraîner le modèle"):
        try:
            version, loss, n, history = train_model(epochs=epochs)
            st.success(f"Modèle {version} entraîné sur {n} lignes. Loss validation : {loss:.6f}")
            st.line_chart(pd.DataFrame(history, columns=["Epoch","Train","Validation"]).set_index("Epoch"))
        except Exception as e:
            st.error(str(e))

elif page == "Investigations":
    anomalies = load_anomalies(1000)
    if anomalies.empty:
        st.info("Aucune anomalie disponible.")
    else:
        ids = anomalies["transaction_id"].tolist()
        selected = st.selectbox("Transaction à investiguer", ids)
        st.dataframe(anomalies[anomalies.transaction_id==selected], use_container_width=True)
        assignee = st.text_input("Analyste responsable")
        if st.button("Ouvrir une investigation"):
            inv_id = open_investigation(selected, assignee)
            st.success(f"Dossier d'investigation #{inv_id} ouvert.")
        with connect() as conn:
            inv = pd.read_sql_query("SELECT * FROM investigations ORDER BY id DESC", conn)
        st.dataframe(inv, use_container_width=True)
        if not inv.empty:
            inv_id = st.selectbox("Dossier", inv.id.tolist())
            note = st.text_area("Note d'investigation")
            author = st.text_input("Auteur de la note", key="note_author")
            if st.button("Ajouter la note") and note.strip():
                add_note(inv_id, author, note)
                st.success("Note enregistrée.")
            label = st.selectbox("Qualification", ["A_ANALYSER","ANOMALIE_JUSTIFIEE","SUSPICION_FRAUDE","FRAUDE_CONFIRmee"])
            reviewer = st.text_input("Validateur", key="reviewer")
            comment = st.text_input("Commentaire", key="label_comment")
            if st.button("Enregistrer la qualification"):
                with connect() as conn:
                    txid = conn.execute("SELECT transaction_id FROM investigations WHERE id=?", (int(inv_id),)).fetchone()[0]
                label_transaction(txid, label, reviewer, comment)
                st.success("Qualification enregistrée.")

elif page == "Assistant d'analyse":
    anomalies = load_anomalies(1000)
    if anomalies.empty:
        st.info("Lancez d'abord une détection.")
    else:
        txid = st.selectbox("Transaction", anomalies.transaction_id.tolist())
        row = anomalies[anomalies.transaction_id==txid].iloc[0].to_dict()
        explanation = explain_transaction(row)
        st.text_area("Analyse explicative (mode local)", explanation, height=280)
        if st.button("Enregistrer l'explication"):
            with connect() as conn:
                from fin_guard.db import now
                conn.execute("INSERT INTO ai_explanations(transaction_id,model_version,explanation,provider,created_at) VALUES(?,?,?,?,?)",
                             (int(txid), row.get("model_version"), explanation, "offline", now()))
            st.success("Explication enregistrée.")

elif page == "Rapports":
    st.subheader("Génération des rapports")
    for label, fn in [("PDF",export_pdf),("Excel",export_excel),("CSV",export_csv)]:
        if st.button(f"Générer {label}"):
            try:
                path = fn()
                st.success(f"Rapport créé : {path}")
                with open(path,"rb") as f:
                    st.download_button(f"Télécharger {label}", f, file_name=path.name)
            except Exception as e:
                st.error(str(e))
