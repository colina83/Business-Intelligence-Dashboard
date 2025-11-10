from django.db import models

class Client(models.Model):
    client_id = models.AutoField(primary_key=True, db_column='ClientID')
    name = models.CharField(max_length=255, db_column='Name')
    
    class Meta:
        db_table = 'clients'
        verbose_name = 'Client'
        verbose_name_plural = 'Clients'
    
    def __str__(self):
        return self.name

class Location(models.Model):
    country_id = models.AutoField(primary_key=True, db_column='CountryID')
    region_id = models.CharField(max_length=100, db_column='RegionID')
    country_name = models.CharField(max_length=255, db_column='CountryName')
    water_depth_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, db_column='WaterDepthMin')
    water_depth_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, db_column='WaterDepthMax')
    
    class Meta:
        db_table = 'locations'
        verbose_name = 'Location'
        verbose_name_plural = 'Locations'
    
    def __str__(self):
        return f"{self.country_name} ({self.region_id})"

class Project(models.Model):
    project_id = models.AutoField(primary_key=True, db_column='ProjectID')
    name = models.CharField(max_length=255, db_column='Name')
    country = models.ForeignKey(Location, on_delete=models.CASCADE, db_column='CountryID', related_name='projects')
    region_id = models.CharField(max_length=100, db_column='RegionID')
    
    class Meta:
        db_table = 'projects'
        verbose_name = 'Project'
        verbose_name_plural = 'Projects'
    
    def __str__(self):
        return self.name

class Opportunity(models.Model):
    BID_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SUBMITTED', 'Submitted'),
        ('WON', 'Won'),
        ('LOST', 'Lost'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    BID_TYPE_CHOICES = [
        ('NEW', 'New'),
        ('RENEWAL', 'Renewal'),
        ('EXTENSION', 'Extension'),
    ]
    
    unique_op_id = models.AutoField(primary_key=True, db_column='UniqueOpID')
    bid_status = models.CharField(max_length=50, choices=BID_STATUS_CHOICES, db_column='BidStatus')
    bid_type = models.CharField(max_length=50, choices=BID_TYPE_CHOICES, db_column='BidType')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, db_column='ClientID', related_name='opportunities')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='opportunities')
    country = models.ForeignKey(Location, on_delete=models.CASCADE, db_column='CountryID', related_name='opportunities')
    region_id = models.CharField(max_length=100, db_column='RegionID')
    
    class Meta:
        db_table = 'opportunities'
        verbose_name = 'Opportunity'
        verbose_name_plural = 'Opportunities'
    
    def __str__(self):
        return f"Opportunity {self.unique_op_id} - {self.client.name}"

class BidMetrics(models.Model):
    opportunity = models.OneToOneField(Opportunity, on_delete=models.CASCADE, primary_key=True, db_column='OpportunityID', related_name='bid_metrics')
    ebit = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='EBIT')
    gp = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='GP')
    gm = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, db_column='GM', help_text='Gross Margin %')
    revenue = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='Revenue')
    cash_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='CashCost')
    duration = models.IntegerField(null=True, blank=True, db_column='Duration', help_text='Duration in days')
    option = models.CharField(max_length=255, null=True, blank=True, db_column='Option')
    
    class Meta:
        db_table = 'bid_metrics'
        verbose_name = 'Bid Metric'
        verbose_name_plural = 'Bid Metrics'
    
    def __str__(self):
        return f"Metrics for Opportunity {self.opportunity.unique_op_id}"

class Equipment(models.Model):
    id = models.AutoField(primary_key=True)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, db_column='OpportunityID', related_name='equipment')
    node_type = models.CharField(max_length=100, db_column='NodeType')
    node_qty = models.IntegerField(db_column='NodeQty')
    vessel_name = models.CharField(max_length=255, null=True, blank=True, db_column='VesselName')
    nhv_qty = models.IntegerField(null=True, blank=True, db_column='NHVQty', help_text='NHV Quantity')
    sv_qty = models.IntegerField(null=True, blank=True, db_column='SVQty', help_text='SV Quantity')
    
    class Meta:
        db_table = 'equipment'
        verbose_name = 'Equipment'
        verbose_name_plural = 'Equipment'
    
    def __str__(self):
        return f"{self.node_type} for Opportunity {self.opportunity.unique_op_id}"

class Milestone(models.Model):
    id = models.AutoField(primary_key=True)
    opportunity = models.OneToOneField(Opportunity, on_delete=models.CASCADE, db_column='OpportunityID', related_name='milestones')
    date_received = models.DateField(null=True, blank=True, db_column='DateReceived')
    date_deadline = models.DateField(null=True, blank=True, db_column='DateDeadline')
    date_award = models.DateField(null=True, blank=True, db_column='DateAward')
    date_contract = models.DateField(null=True, blank=True, db_column='DateContract')
    actual_start = models.DateField(null=True, blank=True, db_column='ActualStart')
    actual_end = models.DateField(null=True, blank=True, db_column='ActualEnd')
    
    class Meta:
        db_table = 'milestones'
        verbose_name = 'Milestone'
        verbose_name_plural = 'Milestones'
    
    def __str__(self):
        return f"Milestones for Opportunity {self.opportunity.unique_op_id}"

class CycleTime(models.Model):
    opportunity = models.OneToOneField(Opportunity, on_delete=models.CASCADE, primary_key=True, db_column='OpportunityID', related_name='cycle_times')
    rec_to_submission = models.IntegerField(null=True, blank=True, db_column='RectoSubmission', help_text='Days from receipt to submission')
    submission_to_award = models.IntegerField(null=True, blank=True, db_column='SubmissionToAward', help_text='Days from submission to award')
    award_to_start = models.IntegerField(null=True, blank=True, db_column='AwardToStart', help_text='Days from award to start')
    total_cycle = models.IntegerField(null=True, blank=True, db_column='TotalCycle', help_text='Total cycle time in days')
    
    class Meta:
        db_table = 'cycle_times'
        verbose_name = 'Cycle Time'
        verbose_name_plural = 'Cycle Times'
    
    def __str__(self):
        return f"Cycle Times for Opportunity {self.opportunity.unique_op_id}"