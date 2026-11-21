// Copyright Epic Games, Inc. All Rights Reserved.

#include "WishCraftCharacter.h"
#include "Engine/LocalPlayer.h"
#include "Camera/CameraComponent.h"
#include "Components/CapsuleComponent.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/SpringArmComponent.h"
#include "GameFramework/Controller.h"
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "InputActionValue.h"
#include "Engine/World.h"
#include "TimerManager.h"
#include "Abilities/HealthComponent.h"
#include "Abilities/AbilityLoadoutManager.h"
#include "WishCraftPlayerController.h"
#include "WishCraftGameMode.h"
#include "WishCraft.h"
#include "WishCraftGameState.h"
#include "Components/SkeletalMeshComponent.h"
#include "Net/UnrealNetwork.h"
#include "Abilities/AbilityComponentBase.h"
#include "Abilities/AbilityTokenTable.h"
#include "NiagaraComponent.h"
#include "NiagaraFunctionLibrary.h"
#include "Components/AudioComponent.h"
#include "Sound/SoundBase.h"
#include "Camera/CameraShakeBase.h"
#include "UObject/ConstructorHelpers.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "SharedCameraManager.h"

AWishCraftCharacter::AWishCraftCharacter()
{
	// Set size for collision capsule
	GetCapsuleComponent()->InitCapsuleSize(42.f, 96.0f);
		
	// Don't rotate when the controller rotates. Let that just affect the camera.
	bUseControllerRotationPitch = false;
	bUseControllerRotationYaw = false;
	bUseControllerRotationRoll = false;

	// Configure character movement
	GetCharacterMovement()->bOrientRotationToMovement = true;
	GetCharacterMovement()->RotationRate = FRotator(0.0f, 500.0f, 0.0f);

	// Note: For faster iteration times these variables, and many more, can be tweaked in the Character Blueprint
	// instead of recompiling to adjust them
	GetCharacterMovement()->JumpZVelocity = 500.f;
	GetCharacterMovement()->AirControl = 0.35f;
	GetCharacterMovement()->MaxWalkSpeed = 500.f;
	GetCharacterMovement()->MinAnalogWalkSpeed = 20.f;
	GetCharacterMovement()->BrakingDecelerationWalking = 2000.f;
	GetCharacterMovement()->BrakingDecelerationFalling = 1500.0f;

	// Create a camera boom (pulls in towards the player if there is a collision)
	CameraBoom = CreateDefaultSubobject<USpringArmComponent>(TEXT("CameraBoom"));
	CameraBoom->SetupAttachment(RootComponent);
	CameraBoom->TargetArmLength = 400.0f;
	CameraBoom->bUsePawnControlRotation = true;

	// Create a follow camera
	FollowCamera = CreateDefaultSubobject<UCameraComponent>(TEXT("FollowCamera"));
	FollowCamera->SetupAttachment(CameraBoom, USpringArmComponent::SocketName);
	FollowCamera->bUsePawnControlRotation = false;

	// Health lives on the character so projectiles and the match flow share one source of truth
	HealthComponent = CreateDefaultSubobject<UHealthComponent>(TEXT("HealthComponent"));

	// Cosmetic feedback. Resolved here rather than on first use, so a wrong path is a startup warning
	// instead of a silent no-op at the exact moment a player is hit.
	static ConstructorHelpers::FObjectFinder<UNiagaraSystem> HitVfxFinder(TEXT("/Game/Sandbox/VFX/NS_Hit_Flash.NS_Hit_Flash"));
	if (HitVfxFinder.Succeeded())
	{
		HitEffect = HitVfxFinder.Object;
	}

	static ConstructorHelpers::FObjectFinder<UNiagaraSystem> DeathVfxFinder(TEXT("/Game/Sandbox/VFX/NS_Death_Dissolve.NS_Death_Dissolve"));
	if (DeathVfxFinder.Succeeded())
	{
		DeathEffect = DeathVfxFinder.Object;
	}

	static ConstructorHelpers::FObjectFinder<UNiagaraSystem> SpawnVfxFinder(TEXT("/Game/Sandbox/VFX/NS_Spawn_In.NS_Spawn_In"));
	if (SpawnVfxFinder.Succeeded())
	{
		SpawnEffect = SpawnVfxFinder.Object;
	}

	static ConstructorHelpers::FObjectFinder<USoundBase> HitAudioFinder(TEXT("/Game/Sandbox/Audio/SFX_Combat_Hit.SFX_Combat_Hit"));
	if (HitAudioFinder.Succeeded())
	{
		HitSound = HitAudioFinder.Object;
	}

	static ConstructorHelpers::FObjectFinder<USoundBase> DeathAudioFinder(TEXT("/Game/Sandbox/Audio/SFX_Combat_Death.SFX_Combat_Death"));
	if (DeathAudioFinder.Succeeded())
	{
		DeathSound = DeathAudioFinder.Object;
	}

	// The reappearance shimmer doubles as the spawn-in stinger: an upward pitch sweep reads as
	// materialising, which is exactly the moment the respawn timer ends.
	static ConstructorHelpers::FObjectFinder<USoundBase> SpawnAudioFinder(TEXT("/Game/Sandbox/Audio/SFX_Ability_InvisibilityOff.SFX_Ability_InvisibilityOff"));
	if (SpawnAudioFinder.Succeeded())
	{
		SpawnSound = SpawnAudioFinder.Object;
	}

	// Note: The skeletal mesh and anim blueprint references on the Mesh component (inherited from Character) 
	// are set in the derived blueprint asset named ThirdPersonCharacter (to avoid direct content references in C++)
}

void AWishCraftCharacter::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);

	DOREPLIFETIME(AWishCraftCharacter, bInvisible);
}

void AWishCraftCharacter::SetInvisible(bool bNewInvisible)
{
	// The server owns the flag; every client mirrors it through OnRep_Invisible
	if (!HasAuthority() || bInvisible == bNewInvisible)
	{
		return;
	}

	bInvisible = bNewInvisible;
	ApplyInvisibleVisual();
}

void AWishCraftCharacter::OnRep_Invisible()
{
	ApplyInvisibleVisual();
}

void AWishCraftCharacter::ApplyInvisibleVisual()
{
	if (USkeletalMeshComponent* CharacterMesh = GetMesh())
	{
		CharacterMesh->SetVisibility(!bInvisible, true);
		UE_LOG(LogSandbox, Log, TEXT("Invisibility %s for %s"), bInvisible ? TEXT("on") : TEXT("off"), *GetNameSafe(this));
	}
}

void AWishCraftCharacter::PlayAbilityEffect(FName Token, bool bStarting)
{
	// Only the server may start the multicast, otherwise every client would send its own copy
	if (!HasAuthority())
	{
		return;
	}

	MulticastAbilityEffect(Token, bStarting);
}

void AWishCraftCharacter::MulticastAbilityEffect_Implementation(FName Token, bool bStarting)
{
	// Runs on the server and on every client that can see this character. The server and the
	// owning client also hold the ability component, so per-ability logic keeps firing there.
	if (UAbilityLoadoutManager* LoadoutManager = GetLoadoutManager())
	{
		if (UAbilityComponentBase* Component = LoadoutManager->FindAbilityComponent(Token))
		{
			Component->HandleCosmetic(bStarting);
		}
	}

	// Every machine that can see this character runs the VFX. The effect is read from the token table
	// rather than from an ability component, which is what lets a viewer with no component render it.
	PlayTokenEffect(Token, bStarting);
	PlayTokenAudio(Token, bStarting);

	// A viewer has no ability component to notify, so the token-level hook is what lets a
	// third party render the effect.
	BP_OnAbilityEffect(Token, bStarting);
	UE_LOG(LogSandbox, Log, TEXT("Ability effect %s %s for %s (%s)"),
		bStarting ? TEXT("on") : TEXT("off"), *Token.ToString(), *GetNameSafe(this),
		HasAuthority() ? TEXT("server") : TEXT("client"));
}

void AWishCraftCharacter::PlayTokenEffect(FName Token, bool bStarting)
{
	UAbilityTokenTable* Table = UAbilityTokenTable::GetDefaultTable();
	const FAbilityTokenDefinition* Definition = Table ? Table->FindToken(Token) : nullptr;

	if (!Definition)
	{
		UE_LOG(LogSandbox, Warning, TEXT("Effect skipped: no token definition for %s"), *Token.ToString());
		return;
	}

	USkeletalMeshComponent* AvatarMesh = GetMesh();
	if (!AvatarMesh)
	{
		return;
	}

	// NAME_None attaches to the mesh origin instead of a bone, which is also a valid choice per token.
	const FName SocketName = Definition->AttachSocket;
	const TCHAR* NetRoleText = HasAuthority() ? TEXT("server") : TEXT("client");

	if (bStarting)
	{
		if (Definition->StartEffect)
		{
			UNiagaraFunctionLibrary::SpawnSystemAttached(
				Definition->StartEffect, AvatarMesh, SocketName, Definition->EffectOffset, FRotator::ZeroRotator,
				EAttachLocation::KeepRelativeOffset, /*bAutoDestroy*/ true);

			UE_LOG(LogSandbox, Log, TEXT("Effect start %s at socket %s on %s (%s)"),
				*Token.ToString(), *SocketName.ToString(), *GetNameSafe(this), NetRoleText);
		}

		if (Definition->SustainEffect)
		{
			// Auto-destroy off: the component is ours to switch off when the ability ends. Leaving it on
			// would let the loop outlive the effect it belongs to.
			UNiagaraComponent* Sustained = UNiagaraFunctionLibrary::SpawnSystemAttached(
				Definition->SustainEffect, AvatarMesh, SocketName, Definition->EffectOffset, FRotator::ZeroRotator,
				EAttachLocation::KeepRelativeOffset, /*bAutoDestroy*/ false);

			if (Sustained)
			{
				TokenEffects.Add(Token, Sustained);
			}

			UE_LOG(LogSandbox, Log, TEXT("Effect sustain %s on %s (%s)"),
				*Token.ToString(), *GetNameSafe(this), NetRoleText);
		}
	}
	else
	{
		// Clear the sustain first, so the loop stops at the same moment the end effect fires.
		if (UNiagaraComponent* Sustained = TokenEffects.FindRef(Token))
		{
			Sustained->Deactivate();
			Sustained->DestroyComponent();
			TokenEffects.Remove(Token);

			UE_LOG(LogSandbox, Log, TEXT("Effect sustain cleared %s on %s (%s)"),
				*Token.ToString(), *GetNameSafe(this), NetRoleText);
		}

		if (Definition->EndEffect)
		{
			UNiagaraFunctionLibrary::SpawnSystemAttached(
				Definition->EndEffect, AvatarMesh, SocketName, Definition->EffectOffset, FRotator::ZeroRotator,
				EAttachLocation::KeepRelativeOffset, /*bAutoDestroy*/ true);

			UE_LOG(LogSandbox, Log, TEXT("Effect end %s on %s (%s)"),
				*Token.ToString(), *GetNameSafe(this), NetRoleText);
		}
	}
}

void AWishCraftCharacter::BeginPlay()
{
	Super::BeginPlay();

	// ApplyDamage only runs on the server, so OnDeath only ever fires there. OnHealthChanged is
	// different: it fires on the server from ApplyDamage and on every client from OnRep_Health,
	// which is exactly what makes damage feedback visible to the player who took it.
	if (HealthComponent)
	{
		HealthComponent->OnDeath.AddDynamic(this, &AWishCraftCharacter::HandleDeath);
		HealthComponent->OnHealthChanged.AddDynamic(this, &AWishCraftCharacter::HandleHealthChanged);
	}

	PlaySpawnFeedback();
}

void AWishCraftCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	// Set up action bindings
	if (UEnhancedInputComponent* EnhancedInputComponent = Cast<UEnhancedInputComponent>(PlayerInputComponent)) {
		
		// Jumping
		EnhancedInputComponent->BindAction(JumpAction, ETriggerEvent::Started, this, &ACharacter::Jump);
		EnhancedInputComponent->BindAction(JumpAction, ETriggerEvent::Completed, this, &ACharacter::StopJumping);

		// Moving
		EnhancedInputComponent->BindAction(MoveAction, ETriggerEvent::Triggered, this, &AWishCraftCharacter::Move);
		EnhancedInputComponent->BindAction(MouseLookAction, ETriggerEvent::Triggered, this, &AWishCraftCharacter::Look);

		// Looking
		EnhancedInputComponent->BindAction(LookAction, ETriggerEvent::Triggered, this, &AWishCraftCharacter::Look);

		// Ability slots - guarded because the assets are assigned on the character Blueprint
		if (AbilityOffenseAction)
		{
			EnhancedInputComponent->BindAction(AbilityOffenseAction, ETriggerEvent::Started, this, &AWishCraftCharacter::OnAbilityOffense);
		}

		if (AbilityDefenseAction)
		{
			EnhancedInputComponent->BindAction(AbilityDefenseAction, ETriggerEvent::Started, this, &AWishCraftCharacter::OnAbilityDefense);
		}

		if (AbilityMobilityAction)
		{
			EnhancedInputComponent->BindAction(AbilityMobilityAction, ETriggerEvent::Started, this, &AWishCraftCharacter::OnAbilityMobility);
		}

		if (RulePanelAction)
		{
			EnhancedInputComponent->BindAction(RulePanelAction, ETriggerEvent::Started, this, &AWishCraftCharacter::OnRulePanel);
		}

		if (ToggleCameraAction)
		{
			EnhancedInputComponent->BindAction(ToggleCameraAction, ETriggerEvent::Started, this, &AWishCraftCharacter::ToggleSharedCamera);
		}
	}
	else
	{
		UE_LOG(LogWishCraft, Error, TEXT("'%s' Failed to find an Enhanced Input component! This template is built to use the Enhanced Input system. If you intend to use the legacy system, then you will need to update this C++ file."), *GetNameSafe(this));
	}
}

/**
 * Each player picks their own view. The preference lives on this machine's PlayerController, so a
 * client switching to the follow camera leaves the host's screen alone. It defaults to the shared
 * framing camera, which is the framing this game is built around.
 */
void AWishCraftCharacter::ToggleSharedCamera()
{
	APlayerController* PlayerController = Cast<APlayerController>(GetController());
	if (!PlayerController || !PlayerController->IsLocalController())
	{
		return;
	}

	// Derive the current mode from the live view target, not from the stored flag: the flag says what
	// was asked for, the view target says what this machine actually has.
	ASharedCameraManager* SharedCamera = Cast<ASharedCameraManager>(
		UGameplayStatics::GetActorOfClass(this, ASharedCameraManager::StaticClass()));

	const bool bIsSharedNow = SharedCamera && PlayerController->GetViewTarget() == SharedCamera;
	const bool bWantsShared = !bIsSharedNow && SharedCamera != nullptr;

	// The controller outlives this pawn, so the choice belongs there; the pawn's flag only mirrors it.
	if (AWishCraftPlayerController* WishController = Cast<AWishCraftPlayerController>(PlayerController))
	{
		WishController->bPrefersSharedCamera = bWantsShared;
	}

	bUsingSharedCamera = bWantsShared;
	ApplyPreferredCamera();
}

void AWishCraftCharacter::ApplyPreferredCamera()
{
	APlayerController* PlayerController = Cast<APlayerController>(GetController());
	if (!PlayerController || !PlayerController->IsLocalController())
	{
		// Not this machine's screen, or between pawns: nothing to aim, so stop retrying.
		if (UWorld* World = GetWorld())
		{
			World->GetTimerManager().ClearTimer(CameraApplyTimer);
		}
		return;
	}

	bool bWantsShared = bUsingSharedCamera;
	if (const AWishCraftPlayerController* WishController = Cast<AWishCraftPlayerController>(PlayerController))
	{
		bWantsShared = WishController->bPrefersSharedCamera;
	}

	ASharedCameraManager* SharedCamera = Cast<ASharedCameraManager>(
		UGameplayStatics::GetActorOfClass(this, ASharedCameraManager::StaticClass()));

	// Otherwise the controller re-targets this pawn the moment it is possessed and undoes the choice.
	PlayerController->bAutoManageActiveCameraTarget = false;

	if (bWantsShared && !SharedCamera)
	{
		// The manager is a separate level actor: on a client it can arrive after this pawn is possessed.
		// The timer started at possession keeps calling back until it exists.
		return;
	}

	if (UWorld* World = GetWorld())
	{
		World->GetTimerManager().ClearTimer(CameraApplyTimer);
	}

	bUsingSharedCamera = bWantsShared;

	AActor* NewViewTarget = bWantsShared ? Cast<AActor>(SharedCamera) : Cast<AActor>(this);
	if (NewViewTarget)
	{
		PlayerController->SetViewTargetWithBlend(NewViewTarget, 0.35f, VTBlend_Cubic);
	}

	UE_LOG(LogWishCraft, Log, TEXT("Camera mode -> %s (local, %s)"),
		bWantsShared ? TEXT("shared") : TEXT("follow"), *GetNameSafe(PlayerController));
}

void AWishCraftCharacter::NotifyControllerChanged()
{
	Super::NotifyControllerChanged();

	// The one hook that fires after possession on BOTH machines: from PossessedBy on the server (which
	// runs after the pawn's BeginPlay, where no controller exists yet) and from OnRep_Controller on a
	// client. It also fires on unpossess, which ApplyPreferredCamera tolerates.
	ApplyPreferredCamera();

	if (UWorld* World = GetWorld())
	{
		World->GetTimerManager().SetTimer(CameraApplyTimer,
			FTimerDelegate::CreateUObject(this, &AWishCraftCharacter::ApplyPreferredCamera), 0.1f, true);
	}
}

UAbilityLoadoutManager* AWishCraftCharacter::GetLoadoutManager() const
{
	if (const AWishCraftPlayerController* OwningController = Cast<AWishCraftPlayerController>(GetController()))
	{
		return OwningController->GetLoadoutManager();
	}

	return nullptr;
}

void AWishCraftCharacter::OnAbilityOffense()
{
	ActivateCategory(EAbilityCategory::Offense);
}

void AWishCraftCharacter::OnAbilityDefense()
{
	ActivateCategory(EAbilityCategory::Defense);
}

void AWishCraftCharacter::OnAbilityMobility()
{
	ActivateCategory(EAbilityCategory::Mobility);
}

void AWishCraftCharacter::ActivateCategory(EAbilityCategory Category)
{
	// Input arrives on the owning client; the manager routes the request to the server
	if (UAbilityLoadoutManager* Manager = GetLoadoutManager())
	{
		Manager->RequestActivateCategory(Category);
	}
}

void AWishCraftCharacter::OnRulePanel()
{
	if (AWishCraftPlayerController* OwningController = Cast<AWishCraftPlayerController>(GetController()))
	{
		OwningController->ToggleRuleWidget();
	}
}

void AWishCraftCharacter::HandleDeath(AActor* Killer)
{
	if (!HasAuthority())
	{
		return;
	}

	MulticastDeathFeedback();

	if (AWishCraftGameMode* GameMode = GetWorld() ? GetWorld()->GetAuthGameMode<AWishCraftGameMode>() : nullptr)
	{
		GameMode->HandlePlayerDeath(this, Killer);
	}
}

void AWishCraftCharacter::Move(const FInputActionValue& Value)
{
	// input is a Vector2D
	FVector2D MovementVector = Value.Get<FVector2D>();

	// route the input
	DoMove(MovementVector.X, MovementVector.Y);
}

void AWishCraftCharacter::Look(const FInputActionValue& Value)
{
	// input is a Vector2D
	FVector2D LookAxisVector = Value.Get<FVector2D>();

	// route the input
	DoLook(LookAxisVector.X, LookAxisVector.Y);
}

/**
 * Returns the framing camera when it currently owns the view. The shared camera is a separate actor,
 * so while it is the view target neither the control rotation nor this character's own rotation is
 * the rotation the player is looking through - the manager pawn is.
 */
static ASharedCameraManager* GetActiveSharedCamera(const AController* Controller)
{
	const APlayerController* PlayerController = Cast<APlayerController>(Controller);
	return PlayerController ? Cast<ASharedCameraManager>(PlayerController->GetViewTarget()) : nullptr;
}

void AWishCraftCharacter::DoMove(float Right, float Forward)
{
	if (GetController() != nullptr)
	{
		// find out which way is forward
		FRotator Rotation = GetController()->GetControlRotation();

		// While the framing camera owns the view, steer by what the camera sees rather than by this
		// character's control rotation, which still belongs to the replaced follow camera.
		if (const APlayerController* PlayerController = Cast<APlayerController>(GetController()))
		{
			if (GetActiveSharedCamera(PlayerController) != nullptr)
			{
				FVector ViewLocation;
				FRotator ViewRotation;
				PlayerController->GetPlayerViewPoint(ViewLocation, ViewRotation);
				Rotation = ViewRotation;
			}
		}

		const FRotator YawRotation(0, Rotation.Yaw, 0);

		// get forward vector
		const FVector ForwardDirection = FRotationMatrix(YawRotation).GetUnitAxis(EAxis::X);

		// get right vector 
		const FVector RightDirection = FRotationMatrix(YawRotation).GetUnitAxis(EAxis::Y);

		// add movement 
		AddMovementInput(ForwardDirection, Forward);
		AddMovementInput(RightDirection, Right);
	}
}

void AWishCraftCharacter::DoLook(float Yaw, float Pitch)
{
	if (GetController() != nullptr)
	{
		// The framing camera is aimed at the midpoint between two players, so it is not ours to
		// rotate: mouse look is ignored while it owns the view.
		if (GetActiveSharedCamera(GetController()) != nullptr)
		{
			return;
		}

		// add yaw and pitch input to controller
		AddControllerYawInput(Yaw);
		AddControllerPitchInput(Pitch);
	}
}

void AWishCraftCharacter::DoJumpStart()
{
	// signal the character to jump
	Jump();
}

void AWishCraftCharacter::DoJumpEnd()
{
	// signal the character to stop jumping
	StopJumping();
}

/**
 * Plays a token's audio on this machine only. The caller is MulticastAbilityEffect, so it runs on the
 * server and on every client that can see this character - the same reach the VFX already have.
 */
void AWishCraftCharacter::PlayTokenAudio(FName Token, bool bStarting)
{
	UAbilityTokenTable* Table = UAbilityTokenTable::GetDefaultTable();
	const FAbilityTokenDefinition* Definition = Table ? Table->FindToken(Token) : nullptr;
	USkeletalMeshComponent* AvatarMesh = GetMesh();

	if (!Definition || !AvatarMesh)
	{
		return;
	}

	const FName SocketName = Definition->AttachSocket;

	if (bStarting)
	{
		if (Definition->StartSound)
		{
			// Same socket as the VFX, so the cast pans with the hand that threw it.
			UGameplayStatics::SpawnSoundAttached(Definition->StartSound, AvatarMesh, SocketName,
				Definition->EffectOffset, FRotator::ZeroRotator, EAttachLocation::KeepRelativeOffset,
				/*bStopWhenAttachedToDestroyed*/ true, 1.f, 1.f, 0.f, nullptr, nullptr, /*bAutoDestroy*/ true);

			UE_LOG(LogSandbox, Log, TEXT("Sound start %s on %s (%s)"),
				*Token.ToString(), *GetNameSafe(this), HasAuthority() ? TEXT("server") : TEXT("client"));
		}

		if (Definition->SustainSound)
		{
			// Auto-destroy off: this loop is ours to stop when the ability ends.
			UAudioComponent* SustainedAudio = UGameplayStatics::SpawnSoundAttached(Definition->SustainSound,
				AvatarMesh, SocketName, Definition->EffectOffset, FRotator::ZeroRotator,
				EAttachLocation::KeepRelativeOffset, /*bStopWhenAttachedToDestroyed*/ true, 1.f, 1.f, 0.f,
				nullptr, nullptr, /*bAutoDestroy*/ false);

			if (SustainedAudio)
			{
				TokenAudio.Add(Token, SustainedAudio);
			}

			UE_LOG(LogSandbox, Log, TEXT("Sound sustain %s on %s (%s)"),
				*Token.ToString(), *GetNameSafe(this), HasAuthority() ? TEXT("server") : TEXT("client"));
		}

		return;
	}

	if (UAudioComponent* SustainedAudio = TokenAudio.FindRef(Token))
	{
		SustainedAudio->Stop();
		SustainedAudio->DestroyComponent();
		TokenAudio.Remove(Token);

		UE_LOG(LogSandbox, Log, TEXT("Sound sustain cleared %s on %s (%s)"),
			*Token.ToString(), *GetNameSafe(this), HasAuthority() ? TEXT("server") : TEXT("client"));
	}

	if (Definition->EndSound)
	{
		UGameplayStatics::SpawnSoundAttached(Definition->EndSound, AvatarMesh, SocketName,
			Definition->EffectOffset, FRotator::ZeroRotator, EAttachLocation::KeepRelativeOffset,
			/*bStopWhenAttachedToDestroyed*/ true, 1.f, 1.f, 0.f, nullptr, nullptr, /*bAutoDestroy*/ true);

		UE_LOG(LogSandbox, Log, TEXT("Sound end %s on %s (%s)"),
			*Token.ToString(), *GetNameSafe(this), HasAuthority() ? TEXT("server") : TEXT("client"));
	}
}

/**
 * A fireball impact should be felt, not only seen. Only local controllers are shaken: a remote
 * client's view is its own business, and it plays its own copy of this from its own OnRep.
 */
static void PlayImpactCameraShake(UWorld* World, float Scale)
{
	static TWeakObjectPtr<UClass> CachedShakeClass;
	if (!CachedShakeClass.IsValid())
	{
		CachedShakeClass = LoadClass<UCameraShakeBase>(nullptr, TEXT("/Game/Sandbox/VFX/CS_Impact.CS_Impact_C"));
	}

	UClass* ShakeClass = CachedShakeClass.Get();
	if (!World || !ShakeClass)
	{
		return;
	}

	for (FConstPlayerControllerIterator It = World->GetPlayerControllerIterator(); It; ++It)
	{
		if (APlayerController* PlayerController = It->Get())
		{
			if (PlayerController->IsLocalController())
			{
				PlayerController->ClientStartCameraShake(ShakeClass, Scale);
			}
		}
	}
}

void AWishCraftCharacter::HandleHealthChanged(float NewHealth, float MaxHealth)
{
	// A hit is a decrease. OnHealthChanged also fires for a respawn refill and for the first
	// replicated value, and neither of those should spawn an impact effect.
	const bool bTookDamage = LastKnownHealth >= 0.f && NewHealth < LastKnownHealth;
	LastKnownHealth = NewHealth;

	if (!bTookDamage)
	{
		return;
	}

	if (USkeletalMeshComponent* AvatarMesh = GetMesh())
	{
		if (HitEffect)
		{
			UNiagaraFunctionLibrary::SpawnSystemAttached(HitEffect, AvatarMesh, NAME_None, FVector::ZeroVector,
				FRotator::ZeroRotator, EAttachLocation::KeepRelativeOffset, /*bAutoDestroy*/ true);
		}
	}

	if (HitSound)
	{
		UGameplayStatics::PlaySoundAtLocation(this, HitSound, GetActorLocation());
	}

	PlayImpactCameraShake(GetWorld(), 0.6f);

	UE_LOG(LogSandbox, Log, TEXT("Damage feedback for %s (%.0f/%.0f, %s)"),
		*GetNameSafe(this), NewHealth, MaxHealth, HasAuthority() ? TEXT("server") : TEXT("client"));
}

void AWishCraftCharacter::PlaySpawnFeedback()
{
	if (USkeletalMeshComponent* AvatarMesh = GetMesh())
	{
		if (SpawnEffect)
		{
			UNiagaraFunctionLibrary::SpawnSystemAttached(SpawnEffect, AvatarMesh, NAME_None, FVector::ZeroVector,
				FRotator::ZeroRotator, EAttachLocation::KeepRelativeOffset, /*bAutoDestroy*/ true);
		}
	}

	if (SpawnSound)
	{
		UGameplayStatics::PlaySoundAtLocation(this, SpawnSound, GetActorLocation());
	}
}

void AWishCraftCharacter::MulticastDeathFeedback_Implementation()
{
	if (USkeletalMeshComponent* AvatarMesh = GetMesh())
	{
		if (DeathEffect)
		{
			// World, not relative: the ash should rise and disperse where the player died, not chase a
			// corpse that the respawn timer is about to destroy.
			UNiagaraFunctionLibrary::SpawnSystemAttached(DeathEffect, AvatarMesh, NAME_None, FVector::ZeroVector,
				FRotator::ZeroRotator, EAttachLocation::KeepWorldPosition, /*bAutoDestroy*/ true);
		}
	}

	if (DeathSound)
	{
		UGameplayStatics::PlaySoundAtLocation(this, DeathSound, GetActorLocation());
	}

	UE_LOG(LogSandbox, Log, TEXT("Death feedback for %s (%s)"), *GetNameSafe(this),
		HasAuthority() ? TEXT("server") : TEXT("client"));
}
