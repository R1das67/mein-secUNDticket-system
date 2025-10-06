import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque

import discord
from discord import AuditLogAction, Forbidden, HTTPException, NotFound, app_commands
from discord.ext import commands

# ================= BOT SETUP =================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("Fehlende Umgebungsvariable DISCORD_TOKEN.")

GUILD_ID = 1424384847169847338
BOT_ADMIN_ID = 843180408152784936

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.bans = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ================= PANEL STORAGE =================
panels = {
    "panel1": {"name": None, "embed_text": None, "mod_role_id": None, "category_id": None, "ticket_count": 0},
    "panel2": {"name": None, "embed_text": None, "mod_role_id": None, "category_id": None, "ticket_count": 0},
    "panel3": {"name": None, "embed_text": None, "mod_role_id": None, "category_id": None, "ticket_count": 0},
}

# ================= INVITE/SPAM STORAGE =================
INVITE_SPAM_WINDOW_SECONDS = 20
INVITE_SPAM_THRESHOLD = 5
INVITE_TIMEOUT_HOURS = 1
WEBHOOK_STRIKES_BEFORE_KICK = 3
INVITE_REGEX = re.compile(r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord\.com/invite|discordapp\.com/invite)/[A-Za-z0-9\-]+", re.IGNORECASE)
whitelists: dict[int, set[int]] = defaultdict(set)
blacklists: dict[int, set[int]] = defaultdict(set)
invite_timestamps: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=50))
webhook_strikes: defaultdict[int, int] = defaultdict(int)
existing_webhooks: dict[int, set[int]] = defaultdict(set)
VERBOSE = True

def log(*args):
    if VERBOSE:
        print("[LOG]", *args)

# ================= HELPER FUNCTIONS =================
def is_whitelisted(member: discord.Member) -> bool:
    return member and member.id in whitelists[member.guild.id]

def is_blacklisted(member: discord.Member) -> bool:
    return member and member.id in blacklists[member.guild.id]

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator

def is_bot_admin(ctx: commands.Context) -> bool:
    return ctx.author.id == BOT_ADMIN_ID or (ctx.guild and ctx.author.id == ctx.guild.owner_id)

async def safe_delete_message(msg: discord.Message):
    try:
        await msg.delete()
    except (NotFound, Forbidden):
        pass

async def kick_member(guild: discord.Guild, member: discord.Member, reason: str):
    if not member or is_whitelisted(member):
        return
    try:
        await guild.kick(member, reason=reason)
        log(f"Kicked {member} | Reason: {reason}")
    except (Forbidden, HTTPException) as e:
        log(f"Kick failed for {member}: {e}")

async def ban_member(guild: discord.Guild, member: discord.Member, reason: str, delete_days: int = 0):
    if not member or is_whitelisted(member):
        return
    try:
        await guild.ban(member, reason=reason, delete_message_days=delete_days)
        log(f"Banned {member} | Reason: {reason}")
    except (Forbidden, HTTPException) as e:
        log(f"Ban failed for {member}: {e}")

async def timeout_member(member: discord.Member, hours: int, reason: str):
    if not member or is_whitelisted(member):
        return
    try:
        until = datetime.now(timezone.utc) + timedelta(hours=hours)
        await member.edit(timed_out_until=until, reason=reason)
        log(f"Timed out {member} until {until} | Reason: {reason}")
    except (Forbidden, HTTPException) as e:
        log(f"Timeout failed for {member}: {e}")

async def actor_from_audit_log(guild: discord.Guild, action: AuditLogAction, target_id: int | None = None, within_seconds: int = 10):
    await asyncio.sleep(1)
    try:
        now = datetime.now(timezone.utc)
        async for entry in guild.audit_logs(limit=15, action=action):
            if (now - entry.created_at).total_seconds() > within_seconds:
                continue
            if target_id is not None and getattr(entry.target, "id", None) != target_id:
                continue
            return entry.user
    except Forbidden:
        log("Keine Berechtigung, Audit-Logs zu lesen.")
    return None

# ================= WIZARD / PANEL =================
async def start_panel_wizard(interaction: discord.Interaction, panel_key: str):
    await interaction.response.send_message(f"**Wizard für {panel_key} gestartet!**\nBitte gib den **Panel-Namen** ein:", ephemeral=True)
    def check_name(m):
        return m.author == interaction.user and m.channel == interaction.channel
    try:
        name_msg = await bot.wait_for("message", check=check_name, timeout=120)
        panels[panel_key]["name"] = name_msg.content
        await interaction.followup.send("✅ Name gesetzt! Bitte gib nun den **Embed Text** ein (Paragraph):", ephemeral=True)
        text_msg = await bot.wait_for("message", check=check_name, timeout=300)
        panels[panel_key]["embed_text"] = text_msg.content
        await interaction.followup.send("✅ Embed Text gesetzt! Bitte gib nun die **Mod-Rolle ID** ein:", ephemeral=True)
        role_msg = await bot.wait_for("message", check=check_name, timeout=120)
        panels[panel_key]["mod_role_id"] = int(role_msg.content)
        await interaction.followup.send("✅ Mod-Rolle gesetzt! Bitte gib nun die **Kategorie ID** ein:", ephemeral=True)
        cat_msg = await bot.wait_for("message", check=check_name, timeout=120)
        panels[panel_key]["category_id"] = int(cat_msg.content)
        await interaction.followup.send("Alles gesetzt! Panel fertigstellen?", view=WizardFinishView(panel_key=panel_key), ephemeral=True)
    except asyncio.TimeoutError:
        await interaction.followup.send("⏰ Wizard abgebrochen, Zeit überschritten.", ephemeral=True)

# ================= VIEWS =================
class WizardFinishView(discord.ui.View):
    def __init__(self, panel_key=None):
        super().__init__(timeout=None)
        self.panel_key = panel_key

    @discord.ui.button(label="✅ Yes", style=discord.ButtonStyle.success, custom_id="wizard_yes")
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = panels[self.panel_key]
        embed = discord.Embed(title=f"📨 {panel['name']}", description=panel["embed_text"], color=discord.Color(0x3EB489))
        view = TicketOpenPersistentView(panel_name=self.panel_key)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ Panel {self.panel_key} erstellt!", ephemeral=True)

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.danger, custom_id="wizard_no")
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Wizard wird neu gestartet...", view=GoBackButtonView(panel_key=self.panel_key), ephemeral=True)

class GoBackButtonView(discord.ui.View):
    def __init__(self, panel_key=None):
        super().__init__(timeout=None)
        self.panel_key = panel_key

    @discord.ui.button(label="🔄 Go Back", style=discord.ButtonStyle.secondary, custom_id="wizard_goback")
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_panel_wizard(interaction, self.panel_key)

class TicketOpenPersistentView(discord.ui.View):
    def __init__(self, panel_name=None):
        super().__init__(timeout=None)
        self.panel_name = panel_name

    @discord.ui.button(label="📨 Ticket erstellen", style=discord.ButtonStyle.primary, custom_id="ticket_open")
    async def ticket_open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = panels.get(self.panel_name)
        if not panel or not panel["category_id"]:
            await interaction.response.send_message("❌ Panel ist nicht richtig eingerichtet!", ephemeral=True)
            return
        panel["ticket_count"] += 1
        guild = interaction.guild
        category = guild.get_channel(panel["category_id"])
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }
        if panel["mod_role_id"]:
            mod_role = guild.get_role(panel["mod_role_id"])
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        channel = await guild.create_text_channel(name=f"{self.panel_name}-ticket-{panel['ticket_count']}", category=category, overwrites=overwrites)
        ping_text = f"<@{interaction.user.id}>"
        if panel["mod_role_id"]:
            ping_text = f"<@&{panel['mod_role_id']}> {ping_text}"
        await channel.send(ping_text)
        await interaction.response.send_message(f"✅ Ticket erstellt: {channel.mention}", ephemeral=True)

class TicketClosePersistentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="❌ Ticket schließen", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def ticket_close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ConfirmCloseView(interaction.channel)
        await interaction.response.send_message("Möchten Sie das Ticket wirklich schließen?", view=view, ephemeral=True)

class ConfirmCloseView(discord.ui.View):
    def __init__(self, channel):
        super().__init__(timeout=30)
        self.channel = channel
        self.add_item(ConfirmYesButton(channel))
        self.add_item(ConfirmNoButton())

class ConfirmYesButton(discord.ui.Button):
    def __init__(self, channel):
        super().__init__(label="Ja", style=discord.ButtonStyle.success, custom_id="confirm_yes")
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Ticket wird geschlossen...", ephemeral=True)
        await asyncio.sleep(2)
        await self.channel.delete()

class ConfirmNoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Nein", style=discord.ButtonStyle.secondary, custom_id="confirm_no")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("❌ Ticket bleibt geöffnet.", ephemeral=True)

# ================= PANEL COMMANDS =================
@bot.tree.command(name="edit-panel-1", description="Wizard für Panel 1")
@app_commands.check(is_admin)
async def edit_panel_1(interaction: discord.Interaction):
    await start_panel_wizard(interaction, "panel1")

@bot.tree.command(name="edit-panel-2", description="Wizard für Panel 2")
@app_commands.check(is_admin)
async def edit_panel_2(interaction: discord.Interaction):
    await start_panel_wizard(interaction, "panel2")

@bot.tree.command(name="edit-panel-3", description="Wizard für Panel 3")
@app_commands.check(is_admin)
async def edit_panel_3(interaction: discord.Interaction):
    await start_panel_wizard(interaction, "panel3")

# ================= ERROR HANDLER =================
@edit_panel_1.error
@edit_panel_2.error
@edit_panel_3.error
async def admin_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ Du hast keine Administratorrechte, um diesen Befehl zu nutzen.", ephemeral=True)

# ================= ANTI-SPAM / EVENTS =================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # --- Invite Spam ---
    if INVITE_REGEX.search(message.content) and not is_whitelisted(message.author):
        await safe_delete_message(message)
        now_ts = asyncio.get_event_loop().time()
        dq = invite_timestamps[message.author.id]
        dq.append(now_ts)
        while dq and (now_ts - dq[0]) > INVITE_SPAM_WINDOW_SECONDS:
            dq.popleft()
        if len(dq) >= INVITE_SPAM_THRESHOLD:
            if message.author.guild_permissions.administrator:
                await kick_member(message.guild, message.author, "Invite-Link-Spam (Admin)")
            else:
                await timeout_member(message.author, INVITE_TIMEOUT_HOURS, "Invite-Link-Spam")
            dq.clear()

    await bot.process_commands(message)

# Du kannst hier weitere Events von Bot 2 wie on_webhooks_update, on_member_join, on_member_ban etc. hinzufügen

# ================= START BOT =================
bot.run(TOKEN)
